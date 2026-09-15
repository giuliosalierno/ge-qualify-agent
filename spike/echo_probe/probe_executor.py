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
    build_patch,
)
from probe_select import ACTION_SUBMIT as ACTION_SELECT_SUBMIT
from probe_select import BOUND_PATHS, ORDER, build_select_probe

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

    def _extract_set_command(self, message) -> str | None:
        """Returns the value from a `set <value>` chat message, else None.

        A plain text trigger keeps the test in the hands of whoever is driving
        the GE chat window, with no extra UI to click.
        """
        if message is None or not message.parts:
            return None
        for part in message.parts:
            if not isinstance(part.root, TextPart):
                continue
            text = (part.root.text or "").strip()
            if text.lower().startswith("set "):
                return text[4:].strip()
        return None

    def _extract_sel_command(self, message) -> str | None:
        """Parses a `sel [widget]` chat message for the L12 probe.

        Returns `""` for all widgets, a widget key to isolate one, or `None`
        when this is not a `sel` message. Empty string and `None` mean
        different things here, so the caller must check for `None` explicitly.
        """
        if message is None or not message.parts:
            return None
        for part in message.parts:
            if not isinstance(part.root, TextPart):
                continue
            text = (part.root.text or "").strip().lower()
            if text == "sel":
                return ""
            if text.startswith("sel "):
                arg = text[4:].strip()
                return arg if arg in ORDER else ""
        return None

    def _describe_select_result(self, context: dict[str, Any]) -> str:
        """Reports what each widget actually wrote, and of what JSON type.

        The type is the whole point. `ChoicePicker` renders perfectly well and
        still fails us, because it writes `["x"]` where the record wants a
        scalar. "It rendered" is not the same claim as "it binds a scalar",
        and only the second one resolves L12.
        """
        rows = ["| Widget | Path | Value | JSON type | Scalar? |", "| :--- | :--- | :--- | :--- | :--- |"]

        for key in BOUND_PATHS:
            value = context.get(key)
            kind = type(value).__name__ if value is not None else "absent"
            if isinstance(value, str) and value != "":
                verdict = "YES"
            elif value in ("", None):
                verdict = "empty — not selected?"
            else:
                verdict = "NO"
            rows.append(f"| {key} | /sel/{key} | `{value!r}` | {kind} | {verdict} |")

        subtree = context.get("all")
        return (
            "**L12 select probe — submit received.**\n\n"
            + "\n".join(rows)
            + "\n\nWhole `/sel` subtree as received:\n\n"
            + f"```json\n{json.dumps(subtree, indent=2, default=str)}\n```\n\n"
            + "Full action context:\n\n"
            + f"```json\n{json.dumps(context, indent=2, default=str)}\n```"
        )

    def _build_reply(self, action: dict[str, Any] | None, message) -> list[Part]:
        """Renders the form, acknowledges a submit, or patches a single field."""
        # ------------------------------------------------------------------
        # L12: is there a scalar single-select that renders in GE?
        # ------------------------------------------------------------------
        if action and action.get("name") == ACTION_SELECT_SUBMIT:
            return [
                Part(
                    root=TextPart(
                        text=self._describe_select_result(action.get("context", {}))
                    )
                )
            ]

        variant = self._extract_sel_command(message)
        if variant is not None:
            scope = variant or "all five components"
            return [
                Part(
                    root=TextPart(
                        text=(
                            f"Rendering the L12 select probe ({scope}).\n\n"
                            "Each caption names the component below it. A caption"
                            " with nothing under it means that component did not"
                            " render. Pick values, then press Submit.\n\n"
                            "To isolate one widget, send `sel text`, `sel select`,"
                            " `sel radio`, `sel toggle` or `sel chips`."
                        )
                    )
                ),
                *(
                    create_a2ui_part(msg, version=WIRE_VERSION)
                    for msg in build_select_probe(variant or None)
                ),
            ]

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

        # ------------------------------------------------------------------
        # TASK 0.8: can a later turn patch an already-rendered surface?
        #
        # Gemini Enterprise opens a NEW taskId for every chat message while
        # keeping contextId stable. If a bare updateDataModel — no
        # createSurface, no updateComponents — still moves the rendered field,
        # the surface outlives the task and conversational auto-fill is viable.
        # If it does not, every turn has to repaint the whole form.
        # ------------------------------------------------------------------
        new_value = self._extract_set_command(message)
        if new_value is not None:
            return [
                Part(
                    root=TextPart(
                        text=(
                            f"Patching `/form/account` to `{new_value}`."
                            " Sending an updateDataModel and nothing else —"
                            " no surface, no components. Watch the card."
                        )
                    )
                ),
                create_a2ui_part(
                    build_patch("/form/account", new_value), version=WIRE_VERSION
                ),
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
