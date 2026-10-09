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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from qualify.a2ui.actions import (
    COMMIT_STAGE,
    DISMISS_SIGNIN,
    FINALIZE,
    REQUEST_GUIDANCE,
    REVISE_STAGE,
    SKIP_STAGE,
    ActionEvent,
    ActionOutcome,
    dispatch,
    parse_action,
)
from qualify.a2ui.canvas_probe import (
    PROBE_CANVAS_ECHO,
    PROBE_LIVE_BUMP,
    build_canvas_probe,
    build_live_bump,
    build_live_probe,
    is_live_trigger,
)
from qualify.a2ui.views.events import VIEW_EVENTS
from qualify.agent.commands import match_command, normalize_command
from qualify.a2ui.canvas_probe import describe_echo as describe_canvas_echo
from qualify.a2ui.canvas_probe import is_probe_trigger as is_canvas_probe_trigger
from qualify.a2ui.compiler import (
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
from qualify.a2ui.provenance import missing_required
from qualify.config import (
    a2ui_probes_enabled,
    agent_base_url,
    interactive_views_enabled,
    welcome_menu_enabled,
)
from qualify.connectors.oauth_state import build_signin_url
from qualify.a2ui.systems_extractor import (
    SYSTEMS_STAGE_ID,
    SYSTEMS_TABLE_PATH,
    extract_systems,
    render_systems_table,
)
from qualify.export import render_deliverable
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
    #: The user's own recent messages; what extraction evidence must quote.
    #: Filled with `conversation_history` by `execute_turn` when empty.
    user_history: str = ""


@dataclass
class TurnOutput:
    """The complete result of processing an inbound turn."""

    reply_text: str
    a2ui_messages: list[dict[str, Any]]
    session: Session
    outcome: ActionOutcome | None = None
    drafts: list[FieldDraft] = field(default_factory=list)
    auth_required: bool = False

    @property
    def is_complete(self) -> bool:
        return self.session.is_complete

    @property
    def active_stage(self) -> str:
        return self.session.stage


CAPABILITY_GROUNDING_SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "skills"
    / "ge_capability_grounding"
    / "SKILL.md"
)


def load_instructions() -> str:
    """Loads agent/instructions.md and appends the GCP capability grounding skill."""
    parts: list[str] = []
    if INSTRUCTIONS_PATH.is_file():
        parts.append(INSTRUCTIONS_PATH.read_text(encoding="utf-8"))
    if CAPABILITY_GROUNDING_SKILL_PATH.is_file():
        parts.append(CAPABILITY_GROUNDING_SKILL_PATH.read_text(encoding="utf-8"))
    else:
        # Missing from the image once already (it was .gcloudignore'd); the
        # agent still runs but recommends capabilities without grounding.
        log.warning("Capability grounding skill not found at %s", CAPABILITY_GROUNDING_SKILL_PATH)
    return "\n\n---\n\n".join(parts)


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

    Also announces a completed SharePoint sign-in. That cannot happen when the
    sign-in actually completes — it finishes in a browser tab, and Gemini
    Enterprise neither polls nor accepts a push, so the conversation is idle and
    unreachable. The next thing the user says is the first moment we can speak,
    whatever they happen to say.
    """
    session = get_or_start(store, turn_input.context_id, pack_name=pack_name)

    if turn_input.user_text and not turn_input.conversation_history:
        conversation, user_history = _conversation_with_history(session, turn_input.user_text)
        turn_input = replace(
            turn_input, conversation_history=conversation, user_history=user_history
        )
    if turn_input.user_text:
        # Before the turn runs, so whichever save the turn makes keeps it.
        _remember_turn(session, "user", turn_input.user_text)

    banner = _consume_signin_banner(session)

    output = _run_turn(
        store,
        turn_input,
        session,
        extraction_client=extraction_client,
        chat_client=chat_client,
    )

    if banner:
        output.reply_text = (
            f"{banner}\n\n{output.reply_text}" if output.reply_text else banner
        )
        # `output.session`, not `session`. A handover replaces the session for
        # this context mid-turn, and re-saving the object we started with would
        # silently roll that back.
        store.save(output.session)

    remembered = False
    if turn_input.user_text and output.reply_text:
        _remember_turn(output.session, "assistant", output.reply_text)
        remembered = True

    if _announce_lost_signin(output) or _offer_signin_on_first_reply(output) or remembered:
        store.save(output.session)

    return output


#: Chat turns kept for context (user and assistant messages together).
_HISTORY_TURNS = 6
#: Per-message cap. Assistant replies can be long (banners, briefs); the
#: question at their end is what matters, so they are cut from the front.
_HISTORY_CHARS = {"user": 1200, "assistant": 600}


def _remember_turn(session: Session, role: str, text: str) -> None:
    text = text.strip()
    if not text:
        return
    cap = _HISTORY_CHARS[role]
    if len(text) > cap:
        text = text[:cap] if role == "user" else "…" + text[-cap:]
    session.recent_turns = [*session.recent_turns, {"role": role, "text": text}][
        -_HISTORY_TURNS:
    ]


def _conversation_with_history(session: Session, user_text: str) -> tuple[str, str]:
    """(transcript for the models, the user's words only for evidence checks)."""
    lines = []
    user_lines = []
    for turn in session.recent_turns:
        speaker = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {turn.get('text', '')}")
        if speaker == "User":
            user_lines.append(turn.get("text", ""))
    lines.append(f"User: {user_text}")
    user_lines.append(user_text)
    return "\n\n".join(lines), "\n\n".join(user_lines)


def _offer_signin_on_first_reply(output: TurnOutput) -> bool:
    """Attaches the SharePoint sign-in card to the conversation's first reply.

    One place, once, whatever the first message was (a greeting, `portfolio
    review`, a technical review...), instead of each command asking on its
    own. The card is optional and not dismissible: declining simply means not
    clicking, so it never replaces the answer the user asked for.
    """
    import os as _os  # noqa: PLC0415

    session = output.session
    if session.signin_prompted or session.signin_dismissed:
        return False
    if _os.environ.get("SIGNIN_CARD") == "0":
        return False

    from qualify.connectors import token_vault  # noqa: PLC0415
    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

    if not storage_enabled() or token_vault.has_session(session.context_id):
        return False

    session.signin_prompted = True
    if _offers_signin(output):
        return True

    from qualify.a2ui.signin import build_signin_card  # noqa: PLC0415

    auth_url = build_signin_url(agent_base_url(), session.context_id)
    where = _storage_label() or "SharePoint"
    note = (
        f"🔐 *Optional:* sign in with the card below to save to {where} and open "
        "files from here. Once you're done, reply **signed in**."
    )
    output.reply_text = f"{output.reply_text}\n\n---\n{note}" if output.reply_text else note
    output.a2ui_messages = [*output.a2ui_messages, *build_signin_card(auth_url, dismissible=False)]
    return True


def _offers_signin(output: TurnOutput) -> bool:
    """True when this reply already asks the user to sign in."""
    from qualify.a2ui.signin import SIGNIN_SURFACE_ID  # noqa: PLC0415

    if "/auth?t=" in (output.reply_text or ""):
        return True
    return any(
        m.get("createSurface", {}).get("surfaceId") == SIGNIN_SURFACE_ID
        for m in output.a2ui_messages
    )


def _announce_lost_signin(output: TurnOutput) -> bool:
    """Tells the user, once, that their storage sign-in has ended.

    Tokens live only in this process's memory, so a deploy or restart signs
    everyone out; Entra or Google can also reject a refresh token (expired,
    revoked, password changed) or an access token mid-call (the vault's 401
    hook then clears it). Either way the session record still says
    ``signin_confirmed``, and without this the agent would carry on as if
    connected and fail quietly at the next SharePoint call.

    Checked after the turn so a token rejected *during* this turn is caught
    too. Returns True when the session changed.
    """
    session = output.session
    if not session.signin_confirmed:
        return False

    from qualify.connectors import token_vault  # noqa: PLC0415
    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

    if not storage_enabled() or token_vault.has_session(session.context_id):
        return False

    # Re-arm the "connected" banner for the next sign-in, and keep the
    # intake-start card from appearing on top of this notice.
    session.signin_confirmed = False
    session.signin_prompted = True

    where = _storage_label() or "document storage"
    note = f"🔐 **Your {where} session has expired.**"
    if _offers_signin(output):
        output.reply_text = f"{note}\n\n{output.reply_text}" if output.reply_text else note
        return True

    from qualify.a2ui.signin import build_signin_card  # noqa: PLC0415

    auth_url = build_signin_url(agent_base_url(), session.context_id)
    note += (
        " Your answers are kept. Sign in again below to keep saving and opening files "
        f"in {where}, or [open the sign-in page]({auth_url})."
    )
    output.reply_text = f"{note}\n\n---\n\n{output.reply_text}" if output.reply_text else note
    output.a2ui_messages = [*output.a2ui_messages, *build_signin_card(auth_url, dismissible=False)]
    return True


def _consume_signin_banner(session: Session) -> str | None:
    """Returns the one-time "you are connected" notice, or None.

    Fires on the first turn where a delegated token exists and the user has not
    been told yet. Deliberately not tied to the sign-in card: the user may
    finish sign-in in the browser without ever pressing it.
    """
    if session.signin_confirmed:
        return None

    from qualify.connectors.storage import is_connected  # noqa: PLC0415

    if not is_connected(session.context_id):
        return None

    session.signin_confirmed = True
    # The card has done its job; do not offer it again.
    session.signin_prompted = True

    return "✅ **Microsoft SharePoint connected.**"


def _run_turn(
    store: SessionStore,
    turn_input: TurnInput,
    session: Session,
    *,
    extraction_client: ExtractionClient | None = None,
    chat_client: ChatClient | None = None,
) -> TurnOutput:
    """Produces the turn's reply. See `execute_turn` for the public contract."""
    # -----------------------------------------------------------------------
    # Case 1: An inbound A2UI action event
    # -----------------------------------------------------------------------
    if turn_input.action_data:
        event = parse_action(turn_input.action_data)
        probes = a2ui_probes_enabled()
        if probes and event is not None and event.name == PROBE_CANVAS_ECHO:
            # Diagnostic only: report what the canvas inputs wrote back and
            # leave the session record untouched. The values are user input,
            # so only their keys are logged.
            log.info("Canvas probe echo: keys=%s", sorted(event.context or {}))
            return TurnOutput(
                reply_text=describe_canvas_echo(event.context),
                a2ui_messages=[],
                session=session,
            )
        if probes and event is not None and event.name == PROBE_LIVE_BUMP:
            return TurnOutput(
                reply_text=(
                    "Sent a data-only update to the open **Probe 4** panel. If its "
                    "counter went up and the bar moved, an open panel refreshes in place."
                ),
                a2ui_messages=build_live_bump(event.context),
                session=session,
            )
        if event is not None and event.name == "fetchData":
            # GcbpTable's server-side sort/paging request. Our tables are not
            # sortable; this only arrives from panels rendered by older builds.
            log.info("Ignoring table fetchData from %s", event.surface_id)
            return TurnOutput(
                reply_text="The list is already ranked by priority; column sorting isn't supported.",
                a2ui_messages=[],
                session=session,
            )
        if event is not None and event.name in VIEW_EVENTS:
            view_output = _try_view_event(store, event, session)
            if view_output is not None:
                return view_output
        if event is not None:
            outcome = dispatch(session, event)
            output = _handle_action_outcome(
                session,
                outcome,
                chat_client,
                turn_input.conversation_history,
                store=store,
            )
            store.save(session)
            return output

    # -----------------------------------------------------------------------
    # Case 2: Inbound chat message
    # -----------------------------------------------------------------------
    if _is_chat_skip_intent(turn_input.user_text):
        event = ActionEvent(
            name=SKIP_STAGE,
            context={"stage": session.stage},
            surface_id=session.current_surface_id,
        )
        outcome = dispatch(session, event)
        output = _handle_action_outcome(
            session, outcome, chat_client, turn_input.conversation_history
        )
        store.save(session)
        return output

    # Checked ahead of the SharePoint handler, whose keyword matcher would
    # otherwise claim the phrase for containing "sharepoint" and "sign in".
    probe_output = _try_a2ui_probe(turn_input.user_text, session)
    if probe_output is not None:
        store.save(session)
        return probe_output

    help_output = _try_help_command(turn_input.user_text, session)
    if help_output is not None:
        store.save(session)
        return help_output

    # Ahead of the pending-review picker, which would otherwise read "signed
    # in" as an attempt to pick an opportunity.
    ack_output = _try_signin_ack(store, turn_input.user_text, session)
    if ack_output is not None:
        store.save(ack_output.session)
        return ack_output

    workspace_output = _try_workspace_command(turn_input.user_text, session)
    if workspace_output is not None:
        store.save(session)
        return workspace_output

    portfolio_output = _try_portfolio_review(store, turn_input.user_text, session)
    if portfolio_output is not None:
        store.save(session)
        return portfolio_output

    # Also ahead of SharePoint: "start the technical review for UC-2026-ABC123"
    # reads as a load request to that handler's keyword matcher.
    handover_output = _try_start_tech_review(store, turn_input.user_text, session)
    if handover_output is not None:
        return handover_output

    sp_load_output = _try_load_from_sharepoint(turn_input.user_text, session)
    if sp_load_output is not None:
        store.save(session)
        return sp_load_output

    menu_output = _try_welcome_menu(turn_input.user_text, session)
    if menu_output is not None:
        store.save(session)
        return menu_output

    intake_output = _try_start_intake(store, turn_input.user_text, session)
    if intake_output is not None:
        return intake_output

    signin_output = _maybe_offer_signin(session)
    if signin_output is not None:
        store.save(session)
        return signin_output

    # If the current qualification/review is already complete (all stages committed/skipped),
    # handle "start a new qualification" by resetting the session, or guide the user
    # instead of pretending they are still mid-form on Stage 4.
    if session.is_complete:
        lowered_msg = (turn_input.user_text or "").strip().lower()
        if any(
            kw in lowered_msg
            for kw in (
                "new qualification",
                "new use case",
                "qualify another",
                "start another",
                "start business intake",
                "qualify a new",
            )
        ):
            from qualify.sinks.session import new_session  # noqa: PLC0415

            fresh = new_session(session.context_id, "business")
            fresh.welcome_shown = True
            fresh.signin_prompted = session.signin_prompted
            fresh.signin_dismissed = session.signin_dismissed
            fresh.signin_confirmed = session.signin_confirmed
            session = fresh
        else:
            rec_id = session.record.meta.record_id
            init_name = session.record.meta.initiative_name or "this initiative"
            deliverable_name = (
                "Technical Architecture Dossier"
                if session.pack_name == "tech"
                else "Business Value Brief"
            )
            return TurnOutput(
                reply_text=(
                    f"The **{deliverable_name}** for **{init_name}** (`{rec_id}`) is already complete.\n\n"
                    "Here is what you can do next:\n"
                    "- **Update or fill skipped stages**: Click any **Reopen Stage** / **Fill Skipped Stage** button on the summary card above.\n"
                    "- **Technical Architecture Review (Phase 2)**: Type **`technical review`** (or **`show pending documents to review`**) to evaluate a qualified opportunity.\n"
                    "- **CoE Portfolio Prioritization**: Type **`portfolio review`** to score and rank every qualified opportunity.\n"
                    "- **Start a New Qualification**: Type **`start a new qualification`** to begin a new business intake."
                ),
                a2ui_messages=[],
                session=session,
            )

    a2ui_messages: list[dict[str, Any]] = []
    drafts: list[FieldDraft] = []
    stage = session.pack.stages[session.active_stage]
    is_first_stage_open = (
        session.pack_name == "business"
        and session.active_stage == 0
        and not session.rendered_stages
    )

    # If the surface for the active stage hasn't been emitted yet, build it
    # with a fresh surfaceId so it renders as a new card in the chat flow.
    if session.active_stage not in session.rendered_stages:
        sid = session.next_surface_id()
        a2ui_messages.extend(
            _stage_card(session, sid)
        )
        session.rendered_stages.add(session.active_stage)
    else:
        # Surface already active on client: extract drafts from transcript
        convo = turn_input.conversation_history or (turn_input.user_text or "")
        if (
            extraction_client is not None
            and convo.strip()
            and not _should_skip_extraction(turn_input.user_text)
        ):
            result = extract_drafts(
                stage,
                session.pack,
                convo,
                extraction_client,
                evidence_text=turn_input.user_history or None,
            )
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

            # The Systems & Data matrix is a list of objects, which no form
            # component can edit and the field extractor above cannot see. It
            # is built from the transcript instead and shown back as a locked
            # table. See a2ui/systems_extractor.py for why.
            if stage.id == SYSTEMS_STAGE_ID:
                session.record.technical.systems = extract_systems(
                    convo, extraction_client, session.record.technical.systems
                )
                a2ui_messages.append(
                    build_patch(
                        SYSTEMS_TABLE_PATH,
                        render_systems_table(session.record),
                        surface_id=session.current_surface_id,
                    )
                )

    # Generate conversational reply
    reply_text = _generate_chat_reply(
        session, stage, chat_client, turn_input.user_text, turn_input.conversation_history
    )
    unmentioned = [
        f for f in missing_required(session.record, stage)
        if f.label.lower() not in reply_text.lower()
    ]
    if unmentioned and "continue" in reply_text.lower():
        # The chat model sometimes calls the stage complete while a required
        # field is still empty on the card; the gate would then refuse the
        # Continue click it just invited. Say plainly what is still needed.
        still = ", ".join(f"**{f.label}**" for f in unmentioned)
        reply_text = f"{reply_text}\n\n_Still needed on the card before **Continue**: {still}._"
    if is_first_stage_open and not session.welcome_shown:
        session.welcome_shown = True
        if welcome_menu_enabled():
            # A use case typed straight in: open the intake with one line of
            # orientation, not the full menu text in front of the question.
            reply_text = f"{_INTAKE_FROM_CHAT_NOTE}\n\n{reply_text}"
        else:
            reply_text = f"{_welcome_banner()}\n\n---\n\n{reply_text}"

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
    *,
    store: SessionStore | None = None,
) -> TurnOutput:
    """Produces the TurnOutput for an action dispatch."""
    a2ui_messages: list[dict[str, Any]] = []

    if outcome.action == DISMISS_SIGNIN:
        # Open stage 1 in the same turn. Declining SharePoint should feel like
        # getting started, not like an extra click that returns nothing.
        sid = session.next_surface_id()
        session.rendered_stages.add(session.active_stage)
        a2ui_messages.extend(
            _stage_card(session, sid)
        )
        return TurnOutput(
            reply_text=(
                "No problem — we'll carry on without SharePoint. Your answers "
                "are still saved locally, and you can connect at any point by "
                "typing `save to sharepoint`.\n\n"
                "Let's begin."
            ),
            a2ui_messages=a2ui_messages,
            session=session,
            outcome=outcome,
        )

    if outcome.action in (COMMIT_STAGE, SKIP_STAGE):
        if outcome.advanced:
            if outcome.ready_to_finalize:
                # All stages committed/skipped: emit the complete Markdown Business Value Brief,
                # render a new summary/completion card, and sync to optional Google Sheet + SharePoint.
                from qualify.connectors.storage import sync_to_storage  # noqa: PLC0415
                from qualify.sinks.sheets import sync_to_optional_sheet  # noqa: PLC0415

                sync_to_optional_sheet(session.record)
                sp_res = sync_to_storage(
                    session.record,
                    skipped_stages=session.skipped,
                    context_id=session.context_id,
                    pack_name=session.pack_name,
                )
                _remember_links(session, sp_res)
                use_view = _use_brief_view(session)
                use_ws = _use_tech_workspace(session)
                if use_view:
                    reply_text = _brief_view_headline(session)
                elif use_ws:
                    reply_text = _tech_view_headline(session)
                else:
                    reply_text = render_deliverable(
                        session.pack_name, session.record, skipped_stages=session.skipped
                    )
                base_url = agent_base_url()
                auth_link = build_signin_url(base_url, session.context_id)
                from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

                if not storage_enabled():
                    reply_text += _record_store_saved_note(session)
                elif sp_res and sp_res.auth_mode == "delegated":
                    reply_text += (
                        f"\n\n---\n✅ **Saved to SharePoint**: "
                        f"[Open SharePoint Folder]({sp_res.folder_url})"
                    )
                else:
                    reply_text += (
                        f"\n\n---\n⚠️ **SharePoint Sign-In Required**: Saved to local backup because no active Microsoft session was found. "
                        f"**[Click here to Sign in with Microsoft SharePoint]({auth_link})** to save to the shared SharePoint folder "
                        f"(it will sync automatically upon sign-in, or type `save to sharepoint` anytime)."
                    )
                sid = session.next_surface_id("complete")
                a2ui_messages.extend(
                    _brief_view_messages(session, sid, store)
                    if use_view
                    else _workspace_messages(session, focus="checklist", surface_id=sid)
                    if use_ws
                    else build_completion_surface(
                        session.pack,
                        session.record,
                        surface_id=sid,
                        skipped_stages=session.skipped,
                    )
                )
            else:
                # Stage advanced: render the new stage surface as a new chat message card
                # with compact summary headers for all completed/skipped stages.
                sid = session.next_surface_id()
                session.rendered_stages.add(session.active_stage)
                a2ui_messages.extend(
                    _stage_card(session, sid)
                )
                stage = session.pack.stages[session.active_stage]
                if outcome.skipped and outcome.committed_stage_idx is not None:
                    prev_label = session.pack.stages[outcome.committed_stage_idx].label
                    reply_text = (
                        f"Skipped **{prev_label}** for now (you can reopen and complete it anytime).\n\n"
                        + _stage_intro_text(stage)
                    )
                else:
                    reply_text = _stage_intro_text(stage)
        else:
            # Stage commit/skip blocked (e.g. required fields blank on Stage 1)
            if outcome.action == SKIP_STAGE:
                reply_text = (
                    f"{outcome.message}\n\n"
                    "Please fill in the initiative name and problem description above, or tell me in chat."
                )
            else:
                reply_text = (
                    f"Before we can continue to the next stage, please provide the required information:\n"
                    f"- {outcome.message}\n\n"
                    "You can fill these in directly on the form, tell me in chat, or click **Skip for now** if you need to gather this later."
                )

    elif outcome.action == REVISE_STAGE:
        if outcome.handled:
            sid = session.next_surface_id()
            session.rendered_stages.add(session.active_stage)
            a2ui_messages.extend(
                _stage_card(session, sid)
            )
            stage = session.pack.stages[session.active_stage]
            reply_text = f"Reopened **{stage.label}**. You can review or change your answers below."
        else:
            reply_text = f"Could not reopen stage: {outcome.message}"

    elif outcome.action == REQUEST_GUIDANCE:
        reply_text = f"Here is guidance on that field:\n\n{outcome.message}"

    elif outcome.action == FINALIZE:
        if outcome.ready_to_finalize:
            sid = session.next_surface_id("complete")
            if _use_brief_view(session):
                reply_text = _brief_view_headline(session)
                a2ui_messages.extend(_brief_view_messages(session, sid, store))
            else:
                reply_text = render_deliverable(session.pack_name, session.record)
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


def _stage_intro_text(
    stage: Stage,
    *,
    stage_index: int | None = None,
    total_stages: int | None = None,
) -> str:
    """Default introduction text for a stage when entering it."""
    questions = "\n".join(f"- {q}" for q in stage.chat_questions)
    if stage_index == 0 and total_stages:
        return (
            f"#### 🔍 Stage 1 of {total_stages} — **{stage.label}**\n\n"
            f"Use the card below or reply in chat to confirm:\n{questions}"
        )
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


def _is_chat_skip_intent(user_text: str | None) -> bool:
    """Returns True if the user explicitly asks to skip the active section in chat."""
    if not user_text:
        return False
    stripped = user_text.strip().lower()
    if len(stripped) > 60:
        return False
    skip_phrases = (
        "skip",
        "skip this",
        "skip for now",
        "skip section",
        "skip stage",
        "let's skip",
        "lets skip",
        "skip it",
    )
    return stripped in skip_phrases or any(stripped.startswith(p + " ") for p in skip_phrases)


def _maybe_offer_signin(session: Session) -> TurnOutput | None:
    """Offers the SharePoint connect card once, at the top of a qualification.

    Returns None in every case where the card would be noise:

    * already shown this session
    * the user declined it
    * a delegated token is already vaulted
    * the interview has started, so interrupting would lose the user's place

    Set ``SIGNIN_CARD=0`` to disable the card entirely and fall back to the
    sign-in prompt at save time.
    """
    import os as _os  # noqa: PLC0415

    if _os.environ.get("SIGNIN_CARD") == "0":
        return None

    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

    if not storage_enabled():
        return None

    if session.signin_prompted or session.signin_dismissed:
        return None

    # Only at the very start. Once a stage has been rendered the user is mid
    # thought, and a card that replaces the form would read as losing work.
    if session.active_stage != 0 or session.rendered_stages:
        return None

    from qualify.a2ui.signin import build_signin_card  # noqa: PLC0415
    from qualify.connectors.storage import is_connected  # noqa: PLC0415

    if is_connected(session.context_id):
        return None

    base_url = agent_base_url()
    auth_url = build_signin_url(base_url, session.context_id)

    session.signin_prompted = True

    signin_prompt_body = (
        "Before we start — would you like to connect **Microsoft SharePoint**?\n\n"
        "Signing in now means this qualification will be saved directly to the "
        "shared SharePoint folder when we finish. You can also continue without "
        "it and connect later."
    )
    if not session.welcome_shown:
        session.welcome_shown = True
        reply_text = f"{_welcome_banner()}\n\n---\n\n{signin_prompt_body}"
    else:
        reply_text = signin_prompt_body

    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=build_signin_card(auth_url),
        session=session,
    )


_WELCOME_BANNER = (
    "### 👋 Welcome to the Gemini Enterprise AI Qualification & CoE Agent\n"
    "I support three workflows connected to the shared **Microsoft SharePoint** repository:\n\n"
    "1. **📋 Business Value Intake (Phase 1 — Business Owners)**\n"
    "   Describe a new use case idea below (or fill in the **Stage 1** card) to size annual hours saved, match the right Gemini Enterprise capability (Levels 1–6), and save the **Business Value Brief** to SharePoint.\n"
    "2. **🏗️ Technical Architecture Review (Phase 2 — Solution Architects)**\n"
    "   Type **`technical review`** to list qualified opportunities waiting in SharePoint, pick one by name or ID, and generate a 22-point **Technical Architecture Dossier**.\n"
    "3. **📊 CoE Portfolio Prioritization (Activity 3 — CoE Leads)**\n"
    "   Type **`portfolio review`** to score all SharePoint opportunities on Business Value (1–5) & Feasibility (1–5), segment them into quadrants (*Quick Wins*, *Strategic Bets*, *Departmental Niche*, *Deprioritized*), and publish the **Portfolio Prioritization Report** to SharePoint."
)

#: Banner for deployments without document storage (``STORAGE_PROVIDER=none``),
#: e.g. the go/demos Click-to-Deploy build.
_WELCOME_BANNER_NO_STORAGE = (
    "### 👋 Welcome to the Gemini Enterprise AI Qualification & CoE Agent\n"
    "I support three workflows. Every finished intake and review is saved to the agent's record store:\n\n"
    "1. **📋 Business Value Intake (Phase 1 — Business Owners)**\n"
    "   Describe a new use case idea below (or fill in the **Stage 1** card) to size annual hours saved, match the right Gemini Enterprise capability (Levels 1–6), and produce the **Business Value Brief**.\n"
    "2. **🏗️ Technical Architecture Review (Phase 2 — Solution Architects)**\n"
    "   Type **`technical review`** to list qualified opportunities waiting for review, pick one by name or ID, and generate a 22-point **Technical Architecture Dossier**.\n"
    "3. **📊 CoE Portfolio Prioritization (Activity 3 — CoE Leads)**\n"
    "   Type **`portfolio review`** to score every qualified opportunity on Business Value (1–5) & Feasibility (1–5) and segment them into quadrants (*Quick Wins*, *Strategic Bets*, *Departmental Niche*, *Deprioritized*)."
)


def _welcome_banner() -> str:
    """The welcome text matching this deployment's storage configuration."""
    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

    return _WELCOME_BANNER if storage_enabled() else _WELCOME_BANNER_NO_STORAGE


def _record_store_saved_note(session: Session) -> str:
    """Footer for a finished deliverable when there is no document storage."""
    rec_id = session.record.meta.record_id
    if session.pack_name == "tech":
        nxt = "Type `portfolio review` to see where it ranks."
    else:
        nxt = (
            f"A solution architect can now type `technical review {rec_id}` "
            "to start Phase 2."
        )
    return (
        f"\n\n---\n✅ **Saved** to the agent's record store as `{rec_id}`. {nxt}"
    )


_HELP_PHRASES = (
    "what can you do",
    "what do you do",
    "what can you help with",
    "how can you help",
    "what do you offer",
    "tell me about yourself",
    "how does this work",
    "how to use",
    "help menu",
    "capabilities",
    "what are your capabilities",
    "show commands",
    "commands",
)

#: Only at the very start of a conversation: later, "what is this field for?"
#: is a question about the form and belongs to the chat reply.
_OPENER_HELP_PHRASES = (
    "what can i do",
    "what can i do here",
    "what are you",
    "who are you",
    "what is this",
    "what's this",
    "whats this",
    "how do i start",
    "how do i use this",
)


def _is_help_request(user_text: str | None, *, at_start: bool = False) -> bool:
    """True for "help", "what can you do?", also after a greeting.

    Whole short messages only: "agents run shell commands" is an answer.
    """
    cleaned = _strip_greeting(normalize_command(user_text))
    if not cleaned:
        return False
    phrases = _HELP_PHRASES + _OPENER_HELP_PHRASES if at_start else _HELP_PHRASES
    return cleaned in ("help", "menu", "info") or bool(
        match_command(cleaned, phrases, max_tail_words=2)
    )


def _try_help_command(user_text: str | None, session: Session) -> TurnOutput | None:
    """Returns the Welcome & Capabilities menu when explicitly requested."""
    at_start = _at_conversation_start(session)
    if not user_text or not _is_help_request(user_text, at_start=at_start):
        return None
    session.welcome_shown = True
    if welcome_menu_enabled() and at_start:
        # The menu card already lists the three workflows; the long text
        # banner on top of it would say everything twice.
        reply_text = _WELCOME_MENU_INTRO
    else:
        reply_text = _welcome_banner()
    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=_welcome_menu_messages(session),
        session=session,
    )


_GREETINGS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "hiya",
        "howdy",
        "yo",
        "ciao",
        "hola",
        "salve",
        "bonjour",
        "hallo",
        "buongiorno",
        "greetings",
        "morning",
        "start",
        "begin",
    }
)
_GREETING_PHRASES = frozenset(
    {
        "good morning",
        "good afternoon",
        "good evening",
        "get started",
        "let's start",
        "lets start",
        "let's begin",
        "lets begin",
        "let's go",
        "lets go",
    }
)


#: Words that may follow a greeting without adding meaning ("hi there team").
_GREETING_FILLERS = frozenset({"there", "team", "all", "everyone", "agent", "again", "folks"})


def _strip_greeting(cleaned: str) -> str:
    """Drops a leading greeting ("hello", "good morning there") from normalised text.

    "start"/"begin" are greetings only on their own, never a prefix to strip:
    "start technical review" is a command.
    """
    for phrase in sorted(_GREETING_PHRASES, key=len, reverse=True):
        if cleaned == phrase or cleaned.startswith(phrase + " "):
            cleaned = cleaned[len(phrase) :].strip()
            break
    words = cleaned.split()
    i = 0
    while i < len(words) and (
        (words[i] in _GREETINGS and words[i] not in ("start", "begin"))
        or (i > 0 and words[i] in _GREETING_FILLERS)
    ):
        i += 1
    return " ".join(words[i:])


def _is_greeting(user_text: str | None) -> bool:
    """True for a bare opener such as "hello" or "hi there".

    Deliberately narrow: anything that already describes a use case ("hi, our
    claims team re-keys invoices...") is real intake content and must reach
    the interview rather than a menu.
    """
    if not user_text:
        return False
    cleaned = " ".join(
        user_text.strip().lower().replace(",", " ").strip("?.!👋 ").split()
    )
    if not cleaned:
        return False
    if cleaned in _GREETING_PHRASES:
        return True
    words = cleaned.split()
    if words[0] in _GREETINGS and len(words) <= 3:
        return True
    return _strip_greeting(normalize_command(cleaned)) == "" and cleaned != ""


def _at_conversation_start(session: Session) -> bool:
    """Nothing chosen yet: a fresh business session with no card rendered."""
    return (
        session.pack_name == "business"
        and session.active_stage == 0
        and not session.rendered_stages
        and not session.committed
        and not session.skipped
    )


def _welcome_menu_messages(session: Session) -> list[dict[str, Any]]:
    if not welcome_menu_enabled():
        return []
    from qualify.a2ui.views.welcome import build_welcome_menu  # noqa: PLC0415

    return build_welcome_menu(session.panel_surface_id("welcome"))


_INTAKE_FROM_CHAT_NOTE = (
    "📋 Starting a **Business Value Intake** for this idea. "
    "_Type `help` for the other workflows (technical review, portfolio)._"
)

_WELCOME_MENU_INTRO = (
    "### 👋 Welcome to the Gemini Enterprise AI Qualification & CoE Agent\n"
    "Pick one of the three workflows below, or describe a new use case idea "
    "in chat to go straight into the **Business Value Intake**.\n\n"
    "_You can also type `technical review` or `portfolio review` at any time._"
)


def _try_welcome_menu(user_text: str | None, session: Session) -> TurnOutput | None:
    """Answers an opening greeting with the three-workflow menu card.

    A greeting says nothing about who is speaking, so the menu replaces the
    Stage 1 form that used to open by default: architects and CoE leads no
    longer land in a business owner's intake.
    """
    if not welcome_menu_enabled():
        return None
    if not _at_conversation_start(session) or not _is_greeting(user_text):
        return None
    session.welcome_shown = True
    return TurnOutput(
        reply_text=_WELCOME_MENU_INTRO,
        a2ui_messages=_welcome_menu_messages(session),
        session=session,
    )


_INTAKE_TRIGGERS = (
    "business value intake",
    "business intake",
    "new business intake",
    "new qualification",
    "new intake",
    "phase 1 intake",
    "start phase 1",
)


def _try_start_intake(
    store: SessionStore, user_text: str | None, session: Session
) -> TurnOutput | None:
    """Opens Stage 1 deterministically when the user clicks or types a start-intake command."""
    if not user_text:
        return None
    if not (_at_conversation_start(session) or session.is_complete):
        return None
    if not match_command(user_text, _INTAKE_TRIGGERS, max_tail_words=2):
        return None
    return _start_intake(store, session)


def _start_intake(store: SessionStore, session: Session) -> TurnOutput:
    """Opens the Business Value Intake, from the welcome menu's button.

    The menu stays clickable in the chat history, so this may arrive mid-way
    through something else:

    * a finished intake or review: start a fresh business intake
    * an intake in progress: re-show its current stage rather than reset it
    * a technical review in progress: leave it alone and say so
    """
    from qualify.sinks.session import new_session  # noqa: PLC0415

    session.pending_review_choices = []

    if session.pack_name == "tech" and not session.is_complete:
        name = session.record.meta.initiative_name or session.record.meta.record_id
        store.save(session)
        return TurnOutput(
            reply_text=(
                f"You're in the middle of the technical review for **{name}**. "
                "Finish it first, then type `start a new qualification` to "
                "begin a new business intake."
            ),
            a2ui_messages=[],
            session=session,
        )

    if session.is_complete:
        fresh = new_session(session.context_id, "business")
        fresh.signin_prompted = session.signin_prompted
        fresh.signin_dismissed = session.signin_dismissed
        fresh.signin_confirmed = session.signin_confirmed
        session = fresh

    session.welcome_shown = True

    signin_output = _maybe_offer_signin(session)
    if signin_output is not None:
        store.save(session)
        return signin_output

    stage = session.pack.stages[session.active_stage]
    a2ui_messages: list[dict[str, Any]] = []
    if session.active_stage not in session.rendered_stages:
        sid = session.next_surface_id()
        a2ui_messages.extend(_stage_card(session, sid))
        session.rendered_stages.add(session.active_stage)
        reply_text = _stage_intro_text(
            stage,
            stage_index=session.active_stage,
            total_stages=len(session.pack.stages),
        )
    else:
        reply_text = (
            f"Your intake is already under way — carry on with **{stage.label}** "
            "in the card above."
        )

    store.save(session)
    return TurnOutput(reply_text=reply_text, a2ui_messages=a2ui_messages, session=session)


def _record_has_content(session: Session) -> bool:
    """Returns True if the record holds anything worth writing to SharePoint.

    A committed stage is the strongest signal, but a named initiative counts on
    its own: the user can set the name and ask to save before finishing a stage,
    and refusing that would be surprising.

    The point of this check is narrow — stop the agent creating a folder full of
    "Untitled Initiative" placeholders when a message is misread as a save.
    """
    if session.committed:
        return True
    return bool(session.record.meta.initiative_name)


#: Words that mean "I finished signing in", not "save now".
_SIGNIN_SAVE_VERBS = ("save", "sync", "push", "upload", "write", "store")
_SIGNIN_ACK_PHRASES = ("logged in", "signed in", "log in done", "i'm connected", "im connected")


def _is_signin_ack(user_text: str | None) -> bool:
    text = (user_text or "").strip().lower()
    if not text or any(v in text for v in _SIGNIN_SAVE_VERBS):
        return False
    return any(p in text for p in _SIGNIN_ACK_PHRASES) or text.rstrip("!. ") == "done"


def _try_signin_ack(store: SessionStore, user_text: str | None, session: Session) -> TurnOutput | None:
    """Handles "signed in": resume what was waiting for it, else carry on.

    A command that stopped for a sign-in (portfolio, technical review, the
    SharePoint listing) is re-run, so the user gets what they asked for
    without retyping it. Otherwise: the intake if one is under way, or the
    workflow menu.
    """
    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

    if not storage_enabled() or not _is_signin_ack(user_text):
        return None

    from qualify.connectors.storage import is_connected  # noqa: PLC0415

    session.pending_review_choices = []
    resume = session.resume_command
    if resume and is_connected(session.context_id):
        session.resume_command = None
        session.signin_prompted = True
        if resume == "portfolio review":
            out = _try_portfolio_review(store, resume, session)
        elif resume == "technical review":
            out = _try_start_tech_review(store, resume, session)
        else:
            out = _try_load_from_sharepoint(resume, session)
        if out is not None:
            return out

    if session.rendered_stages or session.committed or not welcome_menu_enabled():
        return _acknowledge_signin(session)

    # Nothing chosen yet: confirm and show the three workflows.
    connected = is_connected(session.context_id)
    session.signin_prompted = True
    if connected:
        session.signin_confirmed = True
    head = (
        "You're connected. What would you like to do?"
        if connected
        else "⚠️ I can't see a completed sign-in yet — the browser tab may have been "
        "closed before Microsoft finished. You can carry on without it."
    )
    return TurnOutput(reply_text=head, a2ui_messages=_welcome_menu_messages(session), session=session)


def _acknowledge_signin(session: Session) -> TurnOutput:
    """Confirms the SharePoint connection and opens the first stage.

    Reached when the user says "signed in" with no save verb. They are reporting
    progress on the connect card, not asking for a write, so the right response
    is to start the interview.

    Checks the vault rather than taking their word for it. A sign-in can fail
    quietly — a closed tab, a denied consent, a container restart that wiped the
    vault — and silently carrying on would hand them a nasty surprise at save
    time. Either way the interview starts; only the wording changes.
    """
    from qualify.connectors.storage import is_connected  # noqa: PLC0415

    a2ui_messages: list[dict[str, Any]] = []

    # The card has served its purpose; make sure it cannot be offered again.
    session.signin_prompted = True

    connected = is_connected(session.context_id)
    if connected:
        # `_consume_signin_banner` may have already said this in the same turn.
        # Saying it twice reads like a bug.
        if session.signin_confirmed:
            header = ""
        else:
            session.signin_confirmed = True
            header = (
                "✅ **SharePoint connected.** Nothing is saved yet — I'll write "
                "the opportunity to the shared SharePoint folder once we're done.\n\n"
            )
    else:
        header = (
            "⚠️ I can't see a completed sign-in yet. That usually means the "
            "browser tab was closed before Microsoft finished, or consent was "
            "declined.\n\nWe can carry on regardless — your answers are kept "
            "locally, and you can connect later by typing `save to sharepoint`."
            "\n\n"
        )

    if not session.rendered_stages:
        sid = session.next_surface_id()
        session.rendered_stages.add(session.active_stage)
        a2ui_messages.extend(
            _stage_card(session, sid)
        )
        reply_text = f"{header}Let's begin."
    else:
        reply_text = f"{header}Carry on where we left off."

    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=a2ui_messages,
        session=session,
    )


def _try_a2ui_probe(user_text: str | None, session: Session) -> TurnOutput | None:
    """Renders the ``openUrl`` diagnostic surface on an exact trigger phrase.

    Deliberately an exact match rather than a keyword search, so that no real
    qualification conversation can trip it.

    The catalog-versus-renderer gap is the reason this exists. Gemini
    Enterprise's composite catalog declares ``openUrl``, but the catalog is a
    description of the schema, not a promise about the renderer. GE has already
    been seen accepting a message and drawing nothing, so the sign-in button
    gets measured before it gets built.

    All probes are off unless ``A2UI_PROBES`` is set (see
    `qualify.config.a2ui_probes_enabled`).
    """
    if not user_text or not a2ui_probes_enabled():
        return None

    if is_canvas_probe_trigger(user_text):
        return TurnOutput(
            reply_text=(
                "**A2UI canvas probe**\n\n"
                "Three test surfaces below. Please report:\n\n"
                "1. **Probe 1** card: does clicking it open a side panel with text?\n"
                "2. **Probe 2** (should open by itself): do the tabs switch? On "
                "*Edit*, do you see the collapsible panel, text field, slider and "
                "**QW** badge? Change the name, drag the slider, press **Save** "
                "and tell me what I reply. On *Portfolio chart* and *Table*, "
                "does anything draw?\n"
                "3. **Probe 3**: does the chart draw inline in the chat?"
            ),
            a2ui_messages=build_canvas_probe(),
            session=session,
        )

    if is_live_trigger(user_text):
        return TurnOutput(
            reply_text=(
                "**A2UI live-refresh probe**\n\n"
                "1. Does **Probe 4** open in the side panel, and does the progress bar draw?\n"
                "2. Press **Refresh this panel** two or three times with the panel open. "
                "Does *Refreshed N times* go up and the bar move, without a new panel?\n"
                "3. In the text below the line: which of heading, bold, link, bullets, "
                "**table**, quote and checkboxes look right?"
            ),
            a2ui_messages=build_live_probe(),
            session=session,
        )

    if user_text.strip().lower() not in ("a2ui probe openurl", "probe openurl"):
        return None


    from qualify.a2ui.signin import build_openurl_probe  # noqa: PLC0415

    base_url = agent_base_url()
    auth_url = build_signin_url(base_url, session.context_id)

    reply_text = (
        "**A2UI `openUrl` probe**\n\n"
        "Three renderings of the same link below. Please report:\n\n"
        "1. Which of the three you can see.\n"
        "2. What happens when you click each one.\n\n"
        "Option 3 is the control and is known to work. If you see nothing at "
        "all, the surface itself failed and the buttons are not the problem."
    )

    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=build_openurl_probe(auth_url),
        session=session,
    )


def _use_brief_view(session: Session) -> bool:
    """The brief panel covers the business pack; tech keeps its card for now."""
    return interactive_views_enabled() and session.pack_name == "business"


def _brief_view_headline(session: Session) -> str:
    from qualify.a2ui.views.portfolio import QUADRANT_ICONS  # noqa: PLC0415
    from qualify.scoring.portfolio import evaluate_opportunity  # noqa: PLC0415

    record = session.record
    ev = evaluate_opportunity(record, apply_to_record=False)
    hours = ev.annual_hours_saved
    return (
        f"✅ **Business Value Brief ready** — opened in the side panel.\n\n"
        f"`{record.meta.record_id}` · {QUADRANT_ICONS[ev.quadrant]} {ev.quadrant} · "
        + (f"{hours:,.0f} hrs/yr" if hours else "hours unsized")
        + f"\n\n➡️ {ev.recommended_next_step}"
    )


def _portfolio_evaluations(store: SessionStore | None) -> list[Any]:
    """Scored finished opportunities, for context in charts. `[]` on any failure."""
    if store is None:
        return []
    items = _portfolio_items_from_record_store(store)
    if not items:
        return []
    from qualify.scoring.portfolio import evaluate_portfolio  # noqa: PLC0415

    try:
        return list(evaluate_portfolio(items).evaluations)
    except Exception as exc:
        log.warning("Portfolio context for the brief chart failed: %s", exc)
        return []


def _brief_view_messages(
    session: Session, surface_id: str, store: SessionStore | None
) -> list[dict[str, Any]]:
    from qualify.a2ui.views.brief import build_brief_view  # noqa: PLC0415

    return build_brief_view(
        session.record,
        surface_id,
        pack=session.pack,
        skipped_stages=session.skipped,
        portfolio=_portfolio_evaluations(store),
        links=session.document_links,
    )


def _try_view_event(
    store: SessionStore, event: ActionEvent, session: Session
) -> TurnOutput | None:
    """Routes a view button onto the handler for the equivalent chat command.

    A click and a typed command take the same path, so the buttons cannot
    drift from what the chat understands.
    """
    from qualify.a2ui.views.events import (  # noqa: PLC0415
        OPEN_BRIEF,
        OPEN_PORTFOLIO,
        OPEN_WORKSPACE,
        START_INTAKE,
        START_TECH_REVIEW,
    )

    record_id = str(event.context.get("recordId") or "").strip()

    if event.name == START_INTAKE:
        return _start_intake(store, session)

    if event.name == OPEN_WORKSPACE:
        output = _open_workspace(session, user_asked_for="progress")
        store.save(output.session)
        return output

    if event.name == OPEN_PORTFOLIO:
        output = _try_portfolio_review(store, "portfolio review", session)
        if output is not None:
            store.save(output.session)
        return output

    if event.name == START_TECH_REVIEW:
        command = f"technical review {record_id}" if record_id else "technical review"
        output = _try_start_tech_review(store, command, session)
        if output is None:
            # Only when a review is already running and no record was named.
            store.save(session)
            return TurnOutput(
                reply_text="You're already in a technical review — carry on in the card above.",
                a2ui_messages=[],
                session=session,
            )
        return output

    if event.name == OPEN_BRIEF and record_id:
        from qualify.a2ui.views.brief import build_brief_view  # noqa: PLC0415
        from qualify.agent.handover import (  # noqa: PLC0415
            load_review_record,
            review_document_links,
        )

        record = load_review_record(store, record_id, session.context_id)
        if record is None:
            return TurnOutput(
                reply_text=f"I couldn't find `{record_id}` in the record store.",
                a2ui_messages=[],
                session=session,
            )
        name = record.meta.initiative_name or record_id
        messages = build_brief_view(
            record,
            session.panel_surface_id("brief"),
            portfolio=_portfolio_evaluations(store),
            links=review_document_links(record_id, context_id=session.context_id, store=store),
        )
        store.save(session)
        return TurnOutput(
            reply_text=f"📄 Opened the brief for **{name}** (`{record_id}`) in the side panel.",
            a2ui_messages=messages,
            session=session,
        )

    return None


def _stage_card(session: Session, sid: str) -> list[dict[str, Any]]:
    """A stage form card, with an *Open workspace* button when views are on."""
    messages = build_surface(
        session.pack,
        session.record,
        session.active_stage,
        surface_id=sid,
        committed_stages=session.committed,
        skipped_stages=session.skipped,
    )
    if not interactive_views_enabled():
        return messages
    from qualify.a2ui.views.workspace import add_open_workspace_button  # noqa: PLC0415

    return add_open_workspace_button(messages, session.record.meta.record_id)


def _remember_links(session: Session, res: Any) -> None:
    """Keeps the storage links a sync returned, for the workspace panel.

    Only real writes count: a mock or queued (unauthenticated) result has no
    file behind its URL.
    """
    if res is None or not getattr(res, "success", False):
        return
    if getattr(res, "auth_mode", "") not in ("delegated", "client_credentials"):
        return
    if res.folder_url:
        session.document_links["folder"] = res.folder_url
    if res.brief_url:
        session.document_links[session.pack_name] = res.brief_url


def _storage_label() -> str | None:
    """The storage product name for links, or None when storage is off."""
    from qualify.connectors.storage import (  # noqa: PLC0415
        get_storage_connector,
        storage_enabled,
    )

    try:
        if not storage_enabled():
            return None
        return get_storage_connector().display_name
    except Exception as exc:  # a broken connector must not break the panel
        log.warning("Storage label unavailable: %s", exc)
        return None


def _workspace_messages(
    session: Session,
    *,
    focus: str = "progress",
    expand_document: str | None = None,
    surface_id: str | None = None,
) -> list[dict[str, Any]]:
    from qualify.a2ui.views.workspace import build_workspace_view  # noqa: PLC0415

    return build_workspace_view(
        pack=session.pack,
        record=session.record,
        committed=session.committed,
        skipped=session.skipped,
        active_stage=session.active_stage,
        surface_id=surface_id or session.panel_surface_id("workspace"),
        complete=session.is_complete,
        links=session.document_links,
        storage_label=_storage_label(),
        focus=focus,
        expand_document=expand_document,
    )


def _open_workspace(session: Session, *, user_asked_for: str) -> TurnOutput:
    focus = "documents" if user_asked_for == "documents" else "progress"
    name = session.record.meta.initiative_name or session.record.meta.record_id
    where = "documents" if focus == "documents" else "progress and documents"
    return TurnOutput(
        reply_text=f"🗂️ Opened the workspace for **{name}** in the side panel ({where}).",
        a2ui_messages=_workspace_messages(session, focus=focus),
        session=session,
    )


_WORKSPACE_TRIGGERS = (
    "workspace",
    "open workspace",
    "show workspace",
    "open the workspace",
    "show the workspace",
    "show progress",
    "show my progress",
    "my progress",
    "progress",
    "where am i",
)
_DOCUMENT_TRIGGERS = (
    "documents",
    "my documents",
    "show documents",
    "show my documents",
    "open documents",
    "show the documents",
)


def _try_workspace_command(user_text: str | None, session: Session) -> TurnOutput | None:
    """Exact short phrases only, so an interview answer cannot trip it."""
    if not user_text or not interactive_views_enabled():
        return None
    phrase = user_text.strip().lower().rstrip("?.! ")
    if phrase in _DOCUMENT_TRIGGERS:
        return _open_workspace(session, user_asked_for="documents")
    if phrase in _WORKSPACE_TRIGGERS:
        return _open_workspace(session, user_asked_for="progress")
    return None


def _use_tech_workspace(session: Session) -> bool:
    """Technical review completion shows the workspace on its Checklist tab."""
    return interactive_views_enabled() and session.pack_name == "tech"


def _tech_view_headline(session: Session) -> str:
    from qualify.export.dossier import _next_step  # noqa: PLC0415
    from qualify.scoring.technical import score_technical  # noqa: PLC0415

    record = session.record
    score = score_technical(record)
    key2 = "Key 2 approved" if score.key2_ready else "Key 2 pending"
    blockers = f" · ⛔ {len(score.blockers)} blocker(s)" if score.blockers else ""
    return (
        "✅ **Technical Architecture Dossier ready** — opened in the side panel.\n\n"
        f"`{record.meta.record_id}` · readiness {score.readiness_pct}% · "
        f"{score.feasibility_profile} · {key2}{blockers}\n\n"
        f"➡️ {_next_step(score)}"
    )


_PORTFOLIO_TRIGGERS = (
    "portfolio review",
    "analyze portfolio",
    "analyse portfolio",
    "portfolio analysis",
    "prioritize portfolio",
    "prioritise portfolio",
    "portfolio prioritization",
    "portfolio prioritisation",
    "coe portfolio prioritization",
    "coe portfolio prioritisation",
    "coe portfolio review",
    "prioritize use cases",
    "prioritise use cases",
    "score backlog",
    "score portfolio",
    "rank portfolio",
    "rank use cases",
    "coe review",
    "activity 3",
)


def _try_portfolio_review(
    store: SessionStore, user_text: str | None, session: Session
) -> TurnOutput | None:
    """Executes Activity 3: Portfolio Prioritization & Analysis across SharePoint opportunities."""
    if not user_text:
        return None

    # Whole short messages only: "we rank use cases by cost today" is an answer.
    if not match_command(user_text, _PORTFOLIO_TRIGGERS, max_tail_words=3):
        return None


    from qualify.a2ui.signin import build_signin_card  # noqa: PLC0415
    from qualify.connectors.storage import (  # noqa: PLC0415
        get_storage_connector,
        storage_enabled,
    )

    if not storage_enabled():
        items = _portfolio_items_from_record_store(store)
        if not items:
            return TurnOutput(
                reply_text=(
                    "No qualified opportunities yet.\n\n"
                    "Complete at least one **Business Value Intake** (Phase 1), "
                    "then run `portfolio review` again."
                ),
                a2ui_messages=[],
                session=session,
            )
        return _render_portfolio(store, session, items, connector=None)

    from qualify.connectors.storage import StorageAuthRequired  # noqa: PLC0415

    connector = get_storage_connector()
    from_record_store = False
    signin_url: str | None = None
    try:
        items = connector.load_all_opportunities(context_id=session.context_id)
    except Exception as exc:
        from qualify.connectors import token_vault  # noqa: PLC0415

        # A 401 mid-listing clears the token (see the vault's response hook)
        # but surfaces here as a generic failure, so "no session left" counts
        # as signed out too.
        if isinstance(exc, StorageAuthRequired) or not token_vault.has_session(
            session.context_id
        ):
            # Not signed in (as opposed to an outage): say so and offer the
            # sign-in, instead of silently showing the record-store copy.
            signin_url = build_signin_url(agent_base_url(), session.context_id)
            session.resume_command = "portfolio review"
        # No SharePoint session (or SharePoint is down). The record store holds
        # every *finished* opportunity too, so the CoE view still works for
        # users who cannot sign in to Microsoft — e.g. go/demo testers.
        items = _portfolio_items_from_record_store(store)
        from_record_store = True
        if not items:
            base_url = agent_base_url()
            auth_url = build_signin_url(base_url, session.context_id)
            session.signin_prompted = True
            session.resume_command = "portfolio review"
            return TurnOutput(
                reply_text=(
                    "Happy to run the **AI CoE Portfolio Prioritization Review** — "
                    "but I couldn't reach SharePoint to load the qualified opportunities. "
                    "Please sign in with Microsoft below, then reply **signed in**."
                ),
                a2ui_messages=build_signin_card(auth_url, dismissible=False),
                session=session,
            )

    if not items:
        return TurnOutput(
            reply_text=(
                "No qualified opportunities were found in SharePoint yet.\n\n"
                "Complete at least one **Business Value Intake** (Phase 1) and save it "
                "to the shared SharePoint folder, then run `portfolio review` again."
            ),
            a2ui_messages=[],
            session=session,
        )

    return _render_portfolio(
        store,
        session,
        items,
        connector=None if from_record_store else connector,
        signin_url=signin_url,
        account_label=getattr(connector, "account_label", "Microsoft"),
    )


def _render_portfolio(
    store: SessionStore,
    session: Session,
    items: list[tuple[Any, dict[str, Any]]],
    *,
    connector: Any | None,
    signin_url: str | None = None,
    account_label: str = "Microsoft",
) -> TurnOutput:
    """Scores `items` and renders the report.

    `connector` is None when the items came from the record store; the report
    is then not published anywhere and says where its data came from.
    `signin_url` is set when that happened because the user is not signed in;
    the reply and the panel then offer the sign-in.
    """
    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415
    from qualify.export.portfolio import render_portfolio_report  # noqa: PLC0415
    from qualify.scoring.portfolio import evaluate_portfolio  # noqa: PLC0415

    from_record_store = connector is None
    summary = evaluate_portfolio(items)

    # The CoE scores are recomputed on every view and shown, not persisted:
    # writing every listed record back from this snapshot raced the business
    # and technical flows and could erase their newer edits.

    # Remember each folder's address in the record store, so the open buttons
    # also work in conversations that are not signed in.
    remember = getattr(store, "remember_folder_url", None)
    if not from_record_store and remember is not None:
        for ev in summary.evaluations:
            if ev.folder_url:
                try:
                    remember(ev.record_id, ev.folder_url)
                except Exception as exc:
                    log.warning("Could not store folder URL for %s: %s", ev.record_id, exc)

    # Save Portfolio_Prioritization_Report.md to SharePoint and include its URL.
    # Skipped when SharePoint was unreachable a moment ago: it would fail again.
    initial_md = render_portfolio_report(summary)
    report_url = None
    if not from_record_store:
        report_url = connector.sync_portfolio_report(
            initial_md, context_id=session.context_id
        )
    final_md = render_portfolio_report(summary, report_url=report_url)
    if report_url:
        connector.sync_portfolio_report(final_md, context_id=session.context_id)

    # Populate pending_review_choices in exact Ranked Portfolio Matrix order (1..N)
    # so the user can reply with any Rank number (1..N), initiative name (e.g. "AAA"), or Record ID.
    session.pending_review_choices = [
        {
            "recordId": ev.record_id,
            "initiativeName": ev.initiative_name,
            "webUrl": ev.folder_url,
            "hasBrief": ev.has_brief,
            "hasDossier": ev.has_dossier,
        }
        for ev in summary.evaluations
    ]

    source_note = None
    if from_record_store and storage_enabled():
        source_note = (
            f"You're not signed in to {_storage_label() or 'document storage'}, so this is "
            "scored from the agent's saved copies (finished intakes only)."
            if signin_url
            else "Scored from the agent's record store (finished intakes only) — "
            "SharePoint was not available for this conversation."
        )
        final_md = f"_{source_note}_\n\n" + final_md
    signin_line = (
        f"\n\n🔐 {source_note} Sign in with {account_label} (button in the panel) to open "
        "the files and publish the report, then reply **signed in** and I'll refresh it."
        if signin_url
        else ""
    )

    if interactive_views_enabled():
        from qualify.a2ui.views.portfolio import (  # noqa: PLC0415
            build_portfolio_view,
            headline,
        )

        reply = (
            f"📊 **Portfolio prioritization** opened in the side panel: {headline(summary)}."
        )
        top = [ev for ev in summary.evaluations if ev.quadrant == "Quick Wins"][:3]
        if top:
            reply += "\n\nTop Quick Wins: " + ", ".join(
                f"**{ev.initiative_name}** (`{ev.record_id}`)" for ev in top
            )
        if report_url:
            reply += f"\n\n[Full report in SharePoint]({report_url})"
        reply += (
            "\n\nUse the **Actions** tab, or type `technical review <name or ID>`."
        )
        reply += signin_line
        return TurnOutput(
            reply_text=reply,
            a2ui_messages=build_portfolio_view(
                summary,
                session.panel_surface_id("portfolio"),
                source_note=source_note,
                signin=(signin_url, account_label) if signin_url else None,
            ),
            session=session,
        )

    return TurnOutput(
        reply_text=final_md + signin_line,
        a2ui_messages=[],
        session=session,
    )


def _portfolio_items_from_record_store(
    store: SessionStore,
) -> list[tuple[Any, dict[str, Any]]]:
    """Finished opportunities from the durable record store, or `[]`.

    Only stores that keep a completion index (`GCSRecordStore`,
    `LocalRecordStore`) can answer; the in-memory store cannot and returns
    nothing, which sends the caller back to the SharePoint sign-in prompt.
    """
    loader = getattr(store, "load_portfolio_items", None)
    if loader is None:
        return []
    try:
        return list(loader())
    except Exception as exc:
        log.warning("Record-store portfolio fallback failed: %s", exc)
        return []


def _try_start_tech_review(
    store: SessionStore, user_text: str | None, session: Session
) -> TurnOutput | None:
    """Switches this conversation to the technical review of a saved record.

    Replaces the session wholesale rather than mutating the current one. The
    reviewer is almost always a different person in a different conversation,
    and whatever empty business session `get_or_start` just created for them is
    not worth preserving.
    """
    from qualify.agent.handover import (  # noqa: PLC0415
        HandoverError,
        baseline_summary,
        list_pending_reviews,
        looks_like_pending_review_pick,
        parse_tech_review_intent,
        resolve_pending_review_choice,
        review_document_links,
        start_tech_review,
    )

    if not user_text:
        return None

    wants, record_id = parse_tech_review_intent(user_text)

    # Already in a technical review: let the normal interview handle the turn
    # rather than restarting it and throwing away the reviewer's answers.
    # Once that review is finished there is nothing left to protect, so a
    # "what is pending?" must list the queue rather than fall through.
    if session.pack_name == "tech" and not session.is_complete and not record_id:
        return None

    # Check if the user is replying to an active pending-review picker list
    if session.pending_review_choices and not record_id:
        lowered = user_text.strip().lower()
        if any(
            cancel_word in lowered
            for cancel_word in (
                "cancel",
                "never mind",
                "nevermind",
                "stop",
                "new intake",
                "business intake",
            )
        ):
            session.pending_review_choices = []
            store.save(session)
            return None

        matched_id = resolve_pending_review_choice(
            user_text, session.pending_review_choices
        )
        if matched_id:
            record_id = matched_id
            wants = True
        elif not wants and not looks_like_pending_review_pick(user_text):
            # Not a pick and not a command: most likely the next interview
            # answer (the portfolio view arms this list too). Drop the picker
            # and let the turn continue rather than trap the user in it.
            session.pending_review_choices = []
            store.save(session)
            return None
        elif not wants:
            first_choice = session.pending_review_choices[0]
            return TurnOutput(
                reply_text=(
                    f"I couldn't match `{user_text.strip()}` to one of the pending opportunities above.\n\n"
                    f"Please reply with the **number** (1–{len(session.pending_review_choices)}), "
                    f"the **initiative name** (e.g. `{first_choice['initiativeName']}`), or the "
                    f"**record ID** (`{first_choice['recordId']}`) — or say `cancel` to start a new business intake."
                ),
                a2ui_messages=[],
                session=session,
            )

    if not wants:
        return None

    if record_id is None:
        pending, reachable = list_pending_reviews(session.context_id, store=store)
        if reachable and pending:
            matched_id = resolve_pending_review_choice(user_text, pending)
            if matched_id:
                record_id = matched_id

    if record_id is None:
        out = _offer_pending_reviews(session, (pending, reachable))
        store.save(session)
        return out

    session.pending_review_choices = []
    try:
        tech_session = start_tech_review(store, session.context_id, record_id)
    except HandoverError as exc:
        store.save(session)
        return TurnOutput(reply_text=str(exc), a2ui_messages=[], session=session)

    tech_session.signin_confirmed = session.signin_confirmed
    tech_session.signin_prompted = session.signin_prompted
    tech_session.signin_dismissed = session.signin_dismissed
    tech_session.document_links.update(
        review_document_links(record_id, context_id=session.context_id, store=store)
    )

    sid = tech_session.next_surface_id()
    tech_session.rendered_stages.add(tech_session.active_stage)
    a2ui_messages = list(
        _stage_card(tech_session, sid)
    )
    panel_note = ""
    if interactive_views_enabled():
        a2ui_messages += _workspace_messages(
            tech_session, focus="documents", expand_document="brief"
        )
        panel_note = (
            "\n\n🗂️ *The business brief is open in the side panel for reference. "
            "Its **Progress** and **Checklist** tabs track this review.*"
        )
    store.save(tech_session)

    stage = tech_session.pack.stages[tech_session.active_stage]
    reply_text = (
        "### 🏗️ Technical Architecture Review\n\n"
        f"{baseline_summary(tech_session)}\n\n"
        "🔒 *The Phase 1 business baseline above is locked. We will now evaluate "
        "technical feasibility across 5 stages (**1. Systems & Data** → **2. Network** → "
        "**3. Security & IAM** → **4. Grounding & Models** → **5. Operational Readiness**) "
        "to produce the **Technical Architecture Dossier**.*\n\n"
        "---\n"
        f"{_stage_intro_text(stage, stage_index=tech_session.active_stage, total_stages=len(tech_session.pack.stages))}"
        f"{panel_note}"
    )

    return TurnOutput(
        reply_text=reply_text,
        a2ui_messages=a2ui_messages,
        session=tech_session,
    )


#: Closing line on every pending-review reply.
#:
#: Repeated deliberately in all three branches: whatever else the reply says,
#: a reviewer who already knows their id must always see how to use it. It also
#: keeps the reply useful when the listing is empty for a reason we got wrong.
_ID_FALLBACK_HINT = (
    "You can also give me the record id directly — it looks like "
    "`UC-2026-A1B2C3` and appears at the top of the Business Value Brief."
)


def _offer_pending_reviews(
    session: Session, pending_and_reachable: tuple[list[dict], bool]
) -> TurnOutput:
    """Answers "which initiative?" by listing what still needs reviewing.

    Three outcomes, kept apart on purpose:

    - **SharePoint unreachable.** Say so. Silently showing an empty list would
      tell a reviewer there is no work when the truth is we could not look,
      and "no work" is a conclusion they would act on.
    - **Nothing pending.** Every opportunity already has a dossier. Worth
      stating plainly, because it is a real and pleasant answer.
    - **Some pending.** List them with their ids.
    """
    pending, reachable = pending_and_reachable

    if not reachable:
        session.pending_review_choices = []
        import os as _os  # noqa: PLC0415
        from qualify.a2ui.signin import build_signin_card  # noqa: PLC0415

        base_url = agent_base_url()
        auth_url = build_signin_url(base_url, session.context_id)
        cards = (
            build_signin_card(auth_url, dismissible=False)
            if _os.environ.get("SIGNIN_CARD") != "0"
            else []
        )
        session.resume_command = "technical review"
        return TurnOutput(
            reply_text=(
                "Happy to start a technical review — but I couldn't reach "
                "SharePoint to see which opportunities are waiting. Please "
                "sign in with Microsoft below, then type `technical review` "
                "again.\n\n" + _ID_FALLBACK_HINT
            ),
            a2ui_messages=cards,
            session=session,
        )

    if not pending:
        session.pending_review_choices = []
        return TurnOutput(
            reply_text=(
                "Nothing is waiting for a technical review — every qualified "
                "opportunity in SharePoint already has a Technical "
                "Architecture Dossier.\n\n" + _ID_FALLBACK_HINT
            ),
            a2ui_messages=[],
            session=session,
        )

    session.pending_review_choices = [
        {
            "recordId": item["recordId"],
            "initiativeName": item.get("initiativeName") or item.get("name") or "",
        }
        for item in pending
        if item.get("recordId")
    ]

    noun = "opportunity has" if len(pending) == 1 else "opportunities have"
    lines = [
        f"{len(pending)} {noun} a Business Value Brief but no Technical "
        f"Architecture Dossier yet:\n"
    ]
    for i, item in enumerate(pending, start=1):
        name = item.get("initiativeName") or item.get("name") or "Untitled"
        rec_id = item["recordId"]
        url = item.get("webUrl")
        link = f" — [Open in SharePoint]({url})" if url else ""
        lines.append(f"{i}. **{name}** (`{rec_id}`){link}")

    lines.append(
        f"\nTell me which one (by name, number, or ID), or say `technical review {pending[0]['recordId']}`."
    )

    return TurnOutput(
        reply_text="\n".join(lines),
        a2ui_messages=[],
        session=session,
    )


def _try_load_from_sharepoint(user_text: str | None, session: Session) -> TurnOutput | None:
    """Detects chat commands to load, list, or save/sync an opportunity with SharePoint."""
    if not user_text:
        return None
    import os as _os  # noqa: PLC0415
    import re  # noqa: PLC0415

    text_lower = user_text.strip().lower()

    from qualify.connectors.storage import storage_enabled  # noqa: PLC0415

    if not storage_enabled():
        if "sharepoint" not in text_lower:
            return None
        return TurnOutput(
            reply_text=(
                "SharePoint isn't connected in this deployment. Every finished "
                "intake and review is saved automatically to the agent's record "
                "store, and the documents are shown in full in this chat.\n\n"
                "Type `technical review` to see what is waiting for review, or "
                "`portfolio review` to rank every qualified use case."
            ),
            a2ui_messages=[],
            session=session,
        )

    # Case B0: the user is telling us they finished signing in.
    #
    # "signed in" and "logged in" used to be treated as save commands. That was
    # reasonable when the only reason to say them was after a save attempt had
    # demanded a login. The connect card now asks at the very start of the
    # conversation, so the same words mean "I'm connected, let's begin" — and
    # reading them as "save now" wrote an empty record to SharePoint.
    if _is_signin_ack(user_text):
        return _acknowledge_signin(session)

    # Case B: an explicit request to save or sync.
    #
    # Note "connect", "login" and "sign in" are deliberately absent. They are
    # requests to authenticate, not to write anything.
    is_save_request = any(
        w in text_lower
        for w in (
            "save to sharepoint",
            "save into sharepoint",
            "save in sharepoint",
            "save it into sharepoint",
            "save it to sharepoint",
            "sync to sharepoint",
            "sync with sharepoint",
            "push to sharepoint",
            "upload to sharepoint",
        )
    ) or ("sharepoint" in text_lower and any(v in text_lower for v in _SIGNIN_SAVE_VERBS))

    if is_save_request:
        if not _record_has_content(session):
            return TurnOutput(
                reply_text=(
                    "There's nothing to save yet — we haven't captured any "
                    "details for this opportunity.\n\n"
                    "Let's work through the qualification first, and I'll write "
                    "it to SharePoint at the end. You can also save at any point "
                    "once we've covered a stage or two."
                ),
                a2ui_messages=[],
                session=session,
            )

        from qualify.connectors.storage import sync_to_storage  # noqa: PLC0415

        sp_res = sync_to_storage(
            session.record,
            skipped_stages=session.skipped,
            context_id=session.context_id,
            pack_name=session.pack_name,
        )
        _remember_links(session, sp_res)
        if sp_res and sp_res.auth_mode == "delegated":
            title = session.record.meta.initiative_name or session.record.meta.record_id or "Opportunity"
            deliverable_label = (
                "Technical Architecture Dossier"
                if session.pack_name == "tech"
                else "Business Value Brief"
            )
            reply_text = (
                f"✅ **Successfully Saved to SharePoint!**\n\n"
                f"- **Initiative**: {title} (`{session.record.meta.record_id}`)\n"
                f"- **SharePoint Folder**: [Open Opportunity Folder in SharePoint]({sp_res.folder_url})\n"
                f"- **{deliverable_label}**: [Open {deliverable_label} in SharePoint]({sp_res.brief_url})\n\n"
                + render_deliverable(
                    session.pack_name, session.record, skipped_stages=session.skipped
                )
            )
            return TurnOutput(
                reply_text=reply_text,
                a2ui_messages=[],
                session=session,
            )

        base_url = agent_base_url()
        auth_link = build_signin_url(base_url, session.context_id)
        reply_text = (
            f"🔐 **Microsoft SharePoint Sign-In Required**\n\n"
            f"To save **{session.record.meta.initiative_name or session.record.meta.record_id}** to the shared SharePoint folder, "
            f"**[sign in with Microsoft ↗]({auth_link})**.\n\n"
            f"As soon as you finish signing in, I'll save this opportunity automatically."
        )
        return TurnOutput(
            reply_text=reply_text,
            a2ui_messages=[],
            session=session,
            auth_required=True,
        )

    # Match explicit SharePoint load/open requests or direct Record ID load
    has_sp_keyword = "sharepoint" in text_lower or "load uc-" in text_lower or "open uc-" in text_lower
    if not has_sp_keyword:
        return None

    from qualify.connectors.storage import get_storage_connector  # noqa: PLC0415

    # Check if user wants to list / search all SharePoint opportunities
    if any(k in text_lower for k in ("list sharepoint", "search sharepoint", "show sharepoint", "sharepoint opportunities")):
        connector = get_storage_connector()
        # `list_opportunities`, not `search_opportunities` — the latter never
        # existed, so this command raised AttributeError into the executor's
        # guard from the day it was written. `tests/test_turn.py` now calls it.
        try:
            items = connector.list_opportunities(context_id=session.context_id)
        except Exception:
            from qualify.a2ui.signin import build_signin_card  # noqa: PLC0415

            base_url = agent_base_url()
            auth_url = build_signin_url(base_url, session.context_id)
            cards = (
                build_signin_card(auth_url, dismissible=False)
                if _os.environ.get("SIGNIN_CARD") != "0"
                else []
            )
            session.resume_command = "list sharepoint"
            return TurnOutput(
                reply_text=(
                    "⚠️ I couldn't reach SharePoint to list the qualified opportunities. "
                    "Please sign in with Microsoft below, then try `list sharepoint` again."
                ),
                a2ui_messages=cards,
                session=session,
            )
        if not items:
            return TurnOutput(
                reply_text=(
                    "Connected to SharePoint, but there are no qualification "
                    "opportunities yet. Finish a business intake and save it "
                    "to create the first one."
                ),
                a2ui_messages=[],
                session=session,
            )
        lines = ["### Qualification opportunities in SharePoint\n"]
        for item in items:
            rec_id = item.get("recordId") or "unknown id"
            name = item.get("initiativeName") or item.get("name") or rec_id
            url = item.get("webUrl")
            link = f" — [Open in SharePoint]({url})" if url else ""
            if item.get("hasDossier"):
                state = "technically reviewed"
            elif item.get("hasBrief"):
                state = "awaiting technical review"
            else:
                state = "no brief saved yet"
            lines.append(f"- **{name}** (`{rec_id}`) · {state}{link}")
        lines.append(
            "\nSay `technical review <id>` to review one, or "
            "`load <id> from sharepoint` to open it."
        )
        return TurnOutput(
            reply_text="\n".join(lines),
            a2ui_messages=[],
            session=session,
        )

    # Extract UC-YYYY-XXXXXX record ID if present
    match_id = re.search(r"(uc-\d{4}-[a-z0-9_-]+)", text_lower)
    if match_id:
        query = match_id.group(1).upper()
    else:
        # Extract phrase after 'load' or 'open' before 'from sharepoint'
        match_phrase = re.search(r"(?:load|open|get|fetch)\s+(?:opportunity\s+)?(.+?)(?:\s+from\s+sharepoint|$)", user_text.strip(), re.IGNORECASE)
        if not match_phrase:
            return None
        query = match_phrase.group(1).strip(" '\"")

    if not query:
        return None

    from qualify.connectors.storage import StorageAuthRequired  # noqa: PLC0415

    connector = get_storage_connector()
    try:
        loaded = connector.load_opportunity(query, context_id=session.context_id)
    except StorageAuthRequired:
        base_url = agent_base_url()
        return TurnOutput(
            reply_text=(
                "🔐 Please **[sign in with Microsoft ↗]"
                f"({build_signin_url(base_url, session.context_id)})** so I can load "
                f"**{query}** from SharePoint, then ask again."
            ),
            a2ui_messages=[],
            session=session,
            auth_required=True,
        )
    if loaded is None:
        return TurnOutput(
            reply_text=f"Could not find an opportunity matching **{query}** in SharePoint.",
            a2ui_messages=[],
            session=session,
        )

    # Preserve session context_id while hydrating the loaded UseCaseRecord
    loaded.meta.context_id = session.context_id
    session.record = loaded
    session.committed = set(range(len(session.pack.stages)))
    sid = session.next_surface_id("complete")
    a2ui_messages = build_completion_surface(
        session.pack,
        session.record,
        surface_id=sid,
        skipped_stages=session.skipped,
    )
    reply = (
        f"Loaded opportunity **{loaded.meta.initiative_name}** (`{loaded.meta.record_id}`) from SharePoint.\n\n"
        + render_deliverable(
            session.pack_name, loaded, skipped_stages=session.skipped
        )
    )
    return TurnOutput(
        reply_text=reply,
        a2ui_messages=a2ui_messages,
        session=session,
    )


def _should_skip_extraction(user_text: str | None) -> bool:
    """Returns True if the user message is a short uncertainty/question phrase with no digits."""
    if not user_text:
        return True
    stripped = user_text.strip().lower()
    if len(stripped) > 120 or any(c.isdigit() for c in stripped):
        return False
    uncertainty_markers = (
        "don't have",
        "dont have",
        "don't know",
        "dont know",
        "not sure",
        "no idea",
        "no number",
        "what do you mean",
        "help",
        "skip",
    )
    return any(m in stripped for m in uncertainty_markers)


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

    # Deterministic fallback when no LLM client is supplied or if Vertex AI times out
    if _should_skip_extraction(user_text) and stage.id == "sizing":
        return (
            "No problem at all — stopwatch precision isn't needed here! Rough ballpark estimates work great:\n"
            "- **Frequency**: Is this closer to a daily task (**5** times/week) or once a week (**1** time/week)?\n"
            "- **Baseline**: Does a typical run take around **30** minutes today?\n"
            "- **Savings**: Would saving **15** minutes per run be a fair conservative target?\n\n"
            "Feel free to reply with your best guess (e.g., *'5 times a week, 30 mins baseline, 15 mins saved'*), "
            "or click **Skip for now** on the form (or reply **'skip'**) to come back to sizing later."
        )

    missing = missing_required(session.record, stage)
    if not user_text or session.active_stage == 0 and not session.committed and not session.record.meta.initiative_name:
        return _stage_intro_text(stage)

    missing_labels = [f.label for f in missing]
    if missing_labels:
        skip_hint = (
            " (or click **Skip for now** if you don't have this yet)"
            if session.active_stage > 0
            else ""
        )
        return (
            f"Thanks. For **{stage.label}**, we still need:\n"
            + "\n".join(f"- {label}" for label in missing_labels)
            + f"\n\nWhen ready, click **Continue** on the form{skip_hint}."
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
                http_options={"timeout": 12000},
            )
        else:
            self._client = genai.Client(http_options={"timeout": 12000})

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
            "and remind them they can also click **Skip for now** on the form (or type 'skip') if they want to come back to this section later.\n"
            "4. If all [MISSING - REQUIRED] fields for this stage are now filled, congratulate them and invite them to click **Continue** on the form card."
        )
        base_config: dict[str, Any] = {
            "system_instruction": system_prompt,
            "temperature": 0.3,
        }
        config = {
            **base_config,
            "thinking_config": {"thinking_level": "LOW"},
        }
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=conversation,
                config=config,
            )
        except Exception as exc:
            err_str = str(exc)
            if "thinking" in err_str.lower() or "invalid_argument" in err_str.lower() or "400" in err_str:
                log.info("Retrying chat reply without thinking_config for model %s", self.model)
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=conversation,
                    config=base_config,
                )
            elif "404" in err_str and self.model != "gemini-3-flash-preview":
                log.warning("Model %s returned 404, falling back to gemini-3-flash-preview", self.model)
                response = self._client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=conversation,
                    config=config,
                )
            else:
                raise
        return (getattr(response, "text", "") or "").strip()

