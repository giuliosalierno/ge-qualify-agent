"""A2A AgentExecutor implementation for the Qualification Agent.

Adapts inbound A2A RequestContext turns into TurnInputs, delegates execution
to the deterministic turn loop, and streams back A2A TextParts and A2UI DataParts.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCard,
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

from qualify.agent.card import WIRE_VERSION
from qualify.agent.turn import (
    ChatClient,
    TurnInput,
    TurnOutput,
    execute_turn,
)
from qualify.a2ui.patcher import ExtractionClient
from qualify.sinks.session import InMemorySessionStore, SessionStore

log = logging.getLogger(__name__)


class QualifyAgentExecutor(AgentExecutor):
    """A2A Executor wrapping the qualification turn loop."""

    def __init__(
        self,
        agent_card: AgentCard,
        *,
        session_store: SessionStore | None = None,
        extraction_client: ExtractionClient | None = None,
        chat_client: ChatClient | None = None,
        pack_name: str = "business",
    ) -> None:
        self._agent_card = agent_card
        self._session_store = (
            session_store if session_store is not None else InMemorySessionStore()
        )
        self._extraction_client = extraction_client
        self._chat_client = chat_client
        self._pack_name = pack_name

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        active_version = try_activate_a2ui_extension(context, self._agent_card)
        log.info(
            "Turn start: context_id=%s requested_exts=%s active_a2ui=%s",
            context.context_id,
            context.requested_extensions,
            active_version,
        )

        message = context.message
        action_data = self._extract_action(message)
        user_text = self._extract_text(message)

        # Context ID is stable across a GE conversation (D15)
        context_id = (
            context.context_id
            or (context.current_task.context_id if context.current_task else None)
            or (message.context_id if message else None)
            or "default"
        )

        self._extract_and_cache_oauth_tokens(message, context_id, context)

        turn_input = TurnInput(
            context_id=context_id,
            user_text=user_text,
            action_data=action_data,
            conversation_history=user_text or "",
        )

        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        final_state = TaskState.completed
        try:
            output: TurnOutput = execute_turn(
                self._session_store,
                turn_input,
                extraction_client=self._extraction_client,
                chat_client=self._chat_client,
                pack_name=self._pack_name,
            )
            parts: list[Part] = [Part(root=TextPart(text=output.reply_text))]
            for a2ui_msg in output.a2ui_messages:
                parts.append(create_a2ui_part(a2ui_msg, version=WIRE_VERSION))
            # Signal Gemini Enterprise to render its native OAuth sign-in prompt in chat
            if getattr(output, "auth_required", False):
                final_state = TaskState.auth_required
                log.info("Turn requires end-user OAuth: emitting TaskState.auth_required for context %s", context_id)
        except Exception as exc:
            log.exception("Unhandled exception in execute_turn for context %s", context_id)
            parts = [
                Part(
                    root=TextPart(
                        text=(
                            "I encountered a temporary issue processing that step, "
                            f"but your progress is saved ({type(exc).__name__}). Please try again."
                        )
                    )
                )
            ]

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            final_state,
            new_agent_parts_message(parts, task.context_id, task.id),
            final=True,
        )

    def _extract_action(self, message: Any) -> dict[str, Any] | None:
        """Pulls an A2UI action out of inbound parts, if present."""
        if message is None or not getattr(message, "parts", None):
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

    def _extract_text(self, message: Any) -> str | None:
        """Pulls concatenated user chat text out of inbound TextParts."""
        if message is None or not getattr(message, "parts", None):
            return None
        texts: list[str] = []
        for part in message.parts:
            if isinstance(part.root, TextPart) and part.root.text:
                texts.append(part.root.text.strip())
        return " ".join(texts) if texts else None

    def _extract_and_cache_oauth_tokens(
        self,
        message: Any,
        context_id: str,
        context: RequestContext | None = None,
    ) -> None:
        """Captures any end-user OAuth token injected by Gemini Enterprise.

        Gemini Enterprise delivers the token from a Discovery Engine `Authorization`
        resource either as an inbound HTTP header (e.g. `X-Serialized-Auth-Tokens`)
        or inside the A2A `message.metadata`. Both surfaces are scanned recursively.
        """
        from qualify.connectors.sharepoint import harvest_microsoft_tokens  # noqa: PLC0415

        # 1. Inbound HTTP headers (A2A DefaultCallContextBuilder stores them in call_context.state)
        headers: dict[str, Any] = {}
        call_context = getattr(context, "call_context", None) if context is not None else None
        state = getattr(call_context, "state", None)
        if isinstance(state, dict):
            raw_headers = state.get("headers")
            if isinstance(raw_headers, dict):
                headers = raw_headers

        if headers:
            # Log header names only (never values) so the GE-injected auth key is discoverable in Cloud Run logs
            log.info("Inbound A2A header keys: %s", sorted(headers.keys()))
            token = harvest_microsoft_tokens(headers, context_id, path="header")
            if token:
                return

        # 2. A2A message metadata (e.g. temp:sharepoint-auth)
        metadata = getattr(message, "metadata", None) if message is not None else None
        if metadata:
            harvest_microsoft_tokens(metadata, context_id, path="metadata")

    async def cancel(self, request: RequestContext, event_queue: EventQueue) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())

