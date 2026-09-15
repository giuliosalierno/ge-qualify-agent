"""A2A executor for the echo probe.

Its entire job is forensic: log everything the client sends, then reply with a
deterministic A2UI form. The interesting output is in the logs, not the UI.
"""

import json
import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    Part,
    Task,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_parts_message, new_task
from a2a.utils.errors import ServerError
from a2ui.a2a.extension import try_activate_a2ui_extension
from a2ui.a2a.parts import create_a2ui_part
from probe_agent import (
    ACTION_SUBMIT,
    WIRE_VERSION,
    build_agent_card,
    build_form_messages,
)

logger = logging.getLogger(__name__)

BANNER = "=" * 72


def _dump(label: str, value: Any) -> None:
    """Logs a labelled JSON blob, one per line, for easy log grepping."""
    try:
        rendered = json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):
        rendered = repr(value)
    logger.info("%s\n--- %s ---\n%s", BANNER, label, rendered)


class EchoProbeExecutor(AgentExecutor):
    """Logs all inbound client state, then renders the probe form."""

    def __init__(self, agent_card):
        self._agent_card = agent_card

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        active_version = try_activate_a2ui_extension(context, self._agent_card)
        logger.info(
            "requested_extensions=%s active_a2ui_version=%s",
            context.requested_extensions,
            active_version,
        )

        message = context.message

        # ------------------------------------------------------------------
        # TASK 0.6: the data model is expected to arrive in message metadata.
        # This is the single most important line of logging in the spike.
        # ------------------------------------------------------------------
        if message is not None:
            _dump("INBOUND message.metadata  <-- sendDataModel echo lands here", message.metadata)
            _dump("INBOUND full message", message.model_dump(exclude_none=True))
        else:
            logger.info("No inbound message on this turn.")

        if context.current_task is not None:
            _dump(
                "INBOUND task.metadata",
                getattr(context.current_task, "metadata", None),
            )

        action = self._extract_action(message)
        if action:
            _dump("INBOUND a2ui action", action)

        # Reply.
        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        parts = self._build_reply(action, message)

        await updater.update_status(
            TaskState.completed,
            new_agent_parts_message(parts, task.context_id, task.id),
            final=True,
        )

    def _extract_action(self, message) -> dict[str, Any] | None:
        """Pulls an A2UI client action out of the inbound parts, if present."""
        if message is None or not message.parts:
            return None
        for part in message.parts:
            if not isinstance(part.root, DataPart):
                continue
            data = part.root.data
            if not isinstance(data, dict):
                continue
            if data.get("version") != WIRE_VERSION:
                continue
            action = data.get("action")
            if isinstance(action, dict) and action.get("name"):
                return action
        return None

    def _build_reply(self, action: dict[str, Any] | None, message) -> list[Part]:
        """Renders the form, or acknowledges a submit."""
        if action and action.get("name") == ACTION_SUBMIT:
            received = json.dumps(action.get("context", {}), indent=2)
            return [
                Part(
                    root=TextPart(
                        text=(
                            "Submit received. The agent saw exactly this:\n\n"
                            f"```json\n{received}\n```"
                        )
                    )
                )
            ]

        # Any other turn: report what we saw, then render the form.
        echoed = self._describe_echo(message)
        parts: list[Part] = [Part(root=TextPart(text=echoed))]
        parts.extend(
            create_a2ui_part(msg, version=WIRE_VERSION) for msg in build_form_messages()
        )
        return parts

    def _describe_echo(self, message) -> str:
        """Summarises, in chat, whether a data model came back with this turn.

        Surfacing the result in the UI as well as the logs means the test can
        be read without tailing Cloud Run.
        """
        metadata = getattr(message, "metadata", None) if message else None
        if not metadata:
            return (
                "**No client metadata on this turn.** Either the surface does not"
                " exist yet, or `sendDataModel` is not echoing. Edit a field and"
                " send another message."
            )

        keys = list(metadata.keys())
        blob = json.dumps(metadata, default=str)
        return (
            f"**Client metadata received.** Keys: `{keys}`\n\n"
            f"```json\n{blob[:1500]}\n```"
        )

    async def cancel(self, request: RequestContext, event_queue: EventQueue) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())
