"""The turn loop for the qualification interview agent.

Ties the deterministic core together:
- inbound messages (A2UI action or plain chat)
- session persistence (keyed on contextId)
- action dispatching (commit_stage, revise_stage, etc.)
- stage gating (missing required fields prevent advance)
- field extraction (reading drafts out of conversation)
- A2UI compilation (surfaces and incremental patches)
- conversational replies (consultative, strictly grounded)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from qualify.a2ui.actions import (
    COMMIT_STAGE,
    FINALIZE,
    REQUEST_GUIDANCE,
    REVISE_STAGE,
    ActionOutcome,
    dispatch,
    parse_action,
)
from qualify.a2ui.compiler import (
    build_collapsed_stage_patch,
    build_completion_surface,
    build_patch,
    build_surface,
    summary_strings,
)
from qualify.a2ui.patcher import (
    ExtractionClient,
    FieldDraft,
    apply_drafts,
    extract_drafts,
)
from qualify.a2ui.provenance import missing_required, unconfirmed_in_stage
from qualify.export.brief import render_business_brief
from qualify.packs.loader import Stage
from qualify.schema.coerce import get_by_path
from qualify.sinks.session import Session, SessionStore, get_or_start

log = logging.getLogger(__name__)

INSTRUCTIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "agent" / "instructions.md"
)


class ChatClient(Protocol):
    """Protocol for generating conversational assistant responses."""

    def reply(
        self,
        *,
        instruction: str,
        conversation: str,
        stage_label: str,
        record_summary: str,
    ) -> str: ...


@dataclass
class TurnInput:
    """An inbound turn from Gemini Enterprise / A2A."""

    context_id: str
    user_text: str | None = None
    action_data: dict[str, Any] | None = None
    conversation_history: str = ""


@dataclass
class TurnOutput:
    """The complete result of processing an inbound turn."""

    reply_text: str
    a2ui_messages: list[dict[str, Any]]
    session: Session
    outcome: ActionOutcome | None = None
    drafts: list[FieldDraft] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.session.is_complete

    @property
    def active_stage(self) -> str:
        return self.session.stage


def load_instructions() -> str:
    """Loads agent/instructions.md."""
    if INSTRUCTIONS_PATH.is_file():
        return INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    return ""


def execute_turn(
    store: SessionStore,
    turn_input: TurnInput,
    *,
    extraction_client: ExtractionClient | None = None,
    chat_client: ChatClient | None = None,
    pack_name: str = "business",
) -> TurnOutput:
    """Executes a single interview turn.

    Handles both action events (e.g. Continue button click) and plain chat.
    All record updates pass through ownership and coercion checks.
    """
    session = get_or_start(store, turn_input.context_id, pack_name=pack_name)

    # -----------------------------------------------------------------------
    # Case 1: An inbound A2UI action event
    # -----------------------------------------------------------------------
    if turn_input.action_data:
        event = parse_action(turn_input.action_data)
        if event is not None:
            outcome = dispatch(session, event)
            output = _handle_action_outcome(
                session, outcome, chat_client, turn_input.conversation_history
            )
            store.save(session)
            return output

    # -----------------------------------------------------------------------
    # Case 2: Inbound chat message
    # -----------------------------------------------------------------------
    a2ui_messages: list[dict[str, Any]] = []
    drafts: list[FieldDraft] = []
    stage = session.pack.stages[session.active_stage]

    # If the surface for the active stage hasn't been emitted yet, build it
    # with a fresh surfaceId so it renders as a new card in the chat flow.
    if session.active_stage not in session.rendered_stages:
        sid = session.next_surface_id()
        a2ui_messages.extend(
            build_surface(session.pack, session.record, session.active_stage, surface_id=sid)
        )
        session.rendered_stages.add(session.active_stage)
    else:
        # Surface already active on client: extract drafts from transcript
        convo = turn_input.conversation_history or (turn_input.user_text or "")
        if extraction_client is not None and convo.strip():
            result = extract_drafts(stage, session.pack, convo, extraction_client)
            drafts = apply_drafts(session.record, result.drafts)

            for d in drafts:
                a2ui_messages.append(
                    build_patch(d.path, d.value, surface_id=session.current_surface_id)
                )

            # If sizing fields updated, update the live summary cards
            if any(
                d.path.startswith("/uc/sizing/") or d.path == "/uc/business/user_count"
                for d in drafts
            ):
                summaries = summary_strings(session.record)
                a2ui_messages.append(
                    build_patch(
                        "/ui/summary/hours_line",
                        summaries["hours_line"],
                        surface_id=session.current_surface_id,
                    )
                )
                a2ui_messages.append(
                    build_patch(
                        "/ui/summary/hours_basis",
                        summaries["hours_basis"],
                        surface_id=session.current_surface_id,
                    )
                )

    # Generate conversational reply
    reply_text = _generate_chat_reply(
        session, stage, chat_client, turn_input.user_text, turn_input.conversation_history
    )

    store.save(session)
    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=a2ui_messages,
        session=session,
        drafts=drafts,
    )


def _handle_action_outcome(
    session: Session,
    outcome: ActionOutcome,
    chat_client: ChatClient | None,
    conversation_history: str,
) -> TurnOutput:
    """Produces the TurnOutput for an action dispatch."""
    a2ui_messages: list[dict[str, Any]] = []

    if outcome.action == COMMIT_STAGE:
        if outcome.advanced:
            if (
                outcome.committed_stage_idx is not None
                and outcome.committed_surface_id
            ):
                a2ui_messages.append(
                    build_collapsed_stage_patch(
                        session.pack,
                        session.record,
                        outcome.committed_stage_idx,
                        surface_id=outcome.committed_surface_id,
                    )
                )

            if outcome.ready_to_finalize:
                # All stages committed: emit the complete Markdown Business Value Brief,
                # render a new summary/completion card, and sync to optional Google Sheet.
                from qualify.sinks.sheets import sync_to_optional_sheet  # noqa: PLC0415

                sync_to_optional_sheet(session.record)
                reply_text = render_business_brief(session.record)
                sid = session.next_surface_id("complete")
                a2ui_messages.extend(
                    build_completion_surface(session.pack, session.record, surface_id=sid)
                )
            else:
                # Stage advanced: render the new stage surface as a new chat message card
                sid = session.next_surface_id()
                session.rendered_stages.add(session.active_stage)
                a2ui_messages.extend(
                    build_surface(
                        session.pack, session.record, session.active_stage, surface_id=sid
                    )
                )
                stage = session.pack.stages[session.active_stage]
                reply_text = _stage_intro_text(stage)
        else:
            # Stage commit failed (e.g. required fields blank or rejected)
            reply_text = (
                f"Before we can continue to the next stage, please provide the required information:\n"
                f"- {outcome.message}\n\n"
                "You can fill these in directly on the form or tell me in chat."
            )

    elif outcome.action == REVISE_STAGE:
        if outcome.handled:
            sid = session.next_surface_id()
            session.rendered_stages.add(session.active_stage)
            a2ui_messages.extend(
                build_surface(
                    session.pack, session.record, session.active_stage, surface_id=sid
                )
            )
            stage = session.pack.stages[session.active_stage]
            reply_text = f"Reopened **{stage.label}**. You can review or change your answers below."
        else:
            reply_text = f"Could not reopen stage: {outcome.message}"

    elif outcome.action == REQUEST_GUIDANCE:
        reply_text = f"Here is guidance on that field:\n\n{outcome.message}"

    elif outcome.action == FINALIZE:
        if outcome.ready_to_finalize:
            reply_text = render_business_brief(session.record)
            sid = session.next_surface_id("complete")
            a2ui_messages.extend(
                build_completion_surface(session.pack, session.record, surface_id=sid)
            )
        else:
            reply_text = f"Cannot finalize yet: {outcome.message}"

    else:
        reply_text = outcome.message or "Action received."

    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=a2ui_messages,
        session=session,
        outcome=outcome,
    )


def _stage_intro_text(stage: Stage) -> str:
    """Default introduction text for a stage when entering it."""
    questions = "\n".join(f"- {q}" for q in stage.chat_questions)
    return (
        f"Moving on to **{stage.label}**.\n\n"
        f"To qualify this part of the workflow:\n{questions}"
    )


def _build_stage_state_summary(session: Session, stage: Stage) -> str:
    """Formats the current stage's filled and missing fields for the chat model."""
    lines = [
        f"Record ID: {session.record.meta.record_id}",
        f"Active Stage ({session.active_stage + 1} of {len(session.pack.stages)}): {stage.label}",
        "Stage Probing Questions:",
    ]
    for q in stage.chat_questions:
        lines.append(f"  - {q}")

    lines.append("\nForm Field Status in Active Stage:")
    for f in stage.fields:
        if f.readonly or not f.path.startswith("/uc/"):
            continue
        val = get_by_path(session.record, f.path)
        if val is not None and val != "" and val != []:
            lines.append(f"  [FILLED] {f.label}: {val!r}")
        elif f.required:
            lines.append(f"  [MISSING - REQUIRED] {f.label} (Help: {f.help or 'none'})")
        else:
            lines.append(f"  [MISSING - OPTIONAL] {f.label}")

    return "\n".join(lines)


def _generate_chat_reply(
    session: Session,
    stage: Stage,
    chat_client: ChatClient | None,
    user_text: str | None,
    conversation_history: str,
) -> str:
    """Generates the chat reply for a conversational turn."""
    if chat_client is not None:
        instructions = load_instructions()
        record_summary = _build_stage_state_summary(session, stage)
        try:
            reply = chat_client.reply(
                instruction=instructions,
                conversation=conversation_history or (user_text or ""),
                stage_label=stage.label,
                record_summary=record_summary,
            )
            if reply and reply.strip():
                return reply.strip()
        except Exception as exc:
            log.warning("ChatClient reply failed, using deterministic fallback: %s", exc)

    # Deterministic fallback when no LLM client is supplied (e.g. unit tests)
    missing = missing_required(session.record, stage)
    if not user_text or session.active_stage == 0 and not session.committed and not session.record.meta.initiative_name:
        return _stage_intro_text(stage)

    missing_labels = [f.label for f in missing]
    if missing_labels:
        return (
            f"Thanks. For **{stage.label}**, we still need:\n"
            + "\n".join(f"- {label}" for label in missing_labels)
            + "\n\nWhen ready, click **Continue** on the form."
        )
    return (
        f"All required fields for **{stage.label}** are filled!\n\n"
        "Please review the form and click **Continue** to confirm."
    )


class GeminiChatClient:
    """Calls Gemini (default: gemini-3.8-flash) for consultative coaching replies."""

    def __init__(self, model: str = "gemini-3.8-flash", client: Any = None) -> None:
        self.model = model
        if client is not None:
            self._client = client
            return
        import os  # noqa: PLC0415
        from google import genai  # noqa: PLC0415

        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        if "gemini-3" in self.model and location != "global":
            location = "global"

        if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE":
            self._client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                location=location,
            )
        else:
            self._client = genai.Client()

    def reply(
        self,
        *,
        instruction: str,
        conversation: str,
        stage_label: str,
        record_summary: str,
    ) -> str:
        system_prompt = (
            f"{instruction}\n\n"
            f"=== CURRENT FORM & STAGE STATE ===\n"
            f"{record_summary}\n\n"
            "=== CONSULTATIVE COACHING RULES ===\n"
            "1. Keep your response concise (2–4 sentences max). Do not output markdown tables.\n"
            "2. Never invent or guess numbers on your own (Zero Extrapolation Rule).\n"
            "3. If the user says they don't know or don't have exact numbers (e.g., 'I don't have them'), "
            "coach them warmly: explain that stopwatch precision isn't needed and suggest simple ballpark ranges "
            "(e.g., 'Is this closer to a daily task (~5 times/week) or once a week? Does a typical run take ~15 minutes or an hour?'), "
            "or offer a conservative placeholder they can confirm or type into the form.\n"
            "4. If all [MISSING - REQUIRED] fields for this stage are now filled, congratulate them and invite them to click **Continue** on the form card."
        )
        config = {
            "system_instruction": system_prompt,
            "temperature": 0.3,
        }
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=conversation,
                config=config,
            )
        except Exception as exc:
            if "404" in str(exc) and self.model != "gemini-3-flash-preview":
                log.warning("Model %s returned 404, falling back to gemini-3-flash-preview", self.model)
                response = self._client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=conversation,
                    config=config,
                )
            else:
                raise
        return (getattr(response, "text", "") or "").strip()

