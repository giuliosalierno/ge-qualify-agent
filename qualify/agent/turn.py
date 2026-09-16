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
    SKIP_STAGE,
    ActionEvent,
    ActionOutcome,
    dispatch,
    parse_action,
)
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

    sp_load_output = _try_load_from_sharepoint(turn_input.user_text, session)
    if sp_load_output is not None:
        store.save(session)
        return sp_load_output

    a2ui_messages: list[dict[str, Any]] = []
    drafts: list[FieldDraft] = []
    stage = session.pack.stages[session.active_stage]

    # If the surface for the active stage hasn't been emitted yet, build it
    # with a fresh surfaceId so it renders as a new card in the chat flow.
    if session.active_stage not in session.rendered_stages:
        sid = session.next_surface_id()
        a2ui_messages.extend(
            build_surface(
                session.pack,
                session.record,
                session.active_stage,
                surface_id=sid,
                committed_stages=session.committed,
                skipped_stages=session.skipped,
            )
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

    if outcome.action in (COMMIT_STAGE, SKIP_STAGE):
        if outcome.advanced:
            if outcome.ready_to_finalize:
                # All stages committed/skipped: emit the complete Markdown Business Value Brief,
                # render a new summary/completion card, and sync to optional Google Sheet + SharePoint.
                from qualify.connectors.sharepoint import sync_to_optional_sharepoint  # noqa: PLC0415
                from qualify.sinks.sheets import sync_to_optional_sheet  # noqa: PLC0415

                import os as _os  # noqa: PLC0415
                import urllib.parse as _up  # noqa: PLC0415
                sync_to_optional_sheet(session.record)
                sp_res = sync_to_optional_sharepoint(
                    session.record,
                    skipped_stages=session.skipped,
                    context_id=session.context_id,
                )
                reply_text = render_business_brief(
                    session.record, skipped_stages=session.skipped
                )
                base_url = _os.environ.get("AGENT_URL", "https://ge-qualify-agent-g22bhpwccq-uc.a.run.app").rstrip("/")
                auth_link = f"{base_url}/auth?context_id={_up.quote(session.context_id)}"
                if sp_res and sp_res.auth_mode == "delegated":
                    reply_text += (
                        f"\n\n---\n✅ **Synced to SharePoint Online (On-Behalf-Of User)**: "
                        f"[Open SharePoint Folder]({sp_res.folder_url})"
                    )
                else:
                    reply_text += (
                        f"\n\n---\n⚠️ **SharePoint User Login Required**: Saved to local backup because no active Microsoft user session was found. "
                        f"**[Click here to Sign in with Microsoft SharePoint]({auth_link})** to sync directly under your user account "
                        f"(it will auto-sync immediately upon sign-in, or type `save to sharepoint` anytime)."
                    )
                sid = session.next_surface_id("complete")
                a2ui_messages.extend(
                    build_completion_surface(
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
                    build_surface(
                        session.pack,
                        session.record,
                        session.active_stage,
                        surface_id=sid,
                        committed_stages=session.committed,
                        skipped_stages=session.skipped,
                    )
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
                build_surface(
                    session.pack,
                    session.record,
                    session.active_stage,
                    surface_id=sid,
                    committed_stages=session.committed,
                    skipped_stages=session.skipped,
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


def _try_load_from_sharepoint(user_text: str | None, session: Session) -> TurnOutput | None:
    """Detects chat commands to load, list, or save/sync an opportunity with SharePoint."""
    if not user_text:
        return None
    import os as _os  # noqa: PLC0415
    import re  # noqa: PLC0415
    import urllib.parse as _up  # noqa: PLC0415

    text_lower = user_text.strip().lower()

    # Case A: User pasted a Microsoft OAuth redirect URL (https://vertexaisearch.cloud.google.com/oauth-redirect?code=...) or raw code
    if "vertexaisearch.cloud.google.com/oauth-redirect" in text_lower or "code=0." in text_lower or user_text.strip().startswith("0.A"):
        from qualify.connectors.sharepoint import (
            exchange_auth_code_for_session,
            sync_to_optional_sharepoint,
        )

        ex_res = exchange_auth_code_for_session(user_text.strip(), context_id=session.context_id)
        if ex_res.get("success"):
            sp_res = sync_to_optional_sharepoint(
                session.record,
                skipped_stages=session.skipped,
                context_id=session.context_id,
            )
            folder_url = (sp_res.folder_url if sp_res else None) or ex_res.get("folderUrl") or "https://zd8vn.sharepoint.com/"
            title = session.record.meta.initiative_name or session.record.meta.record_id or "Opportunity"
            reply_text = (
                f"✅ **Microsoft SharePoint Connected & Opportunity Saved!**\n\n"
                f"- **Initiative**: {title} (`{session.record.meta.record_id}`)\n"
                f"- **SharePoint Folder**: [Open Opportunity Folder in SharePoint]({folder_url})\n\n"
                + render_business_brief(session.record, skipped_stages=session.skipped)
            )
            return TurnOutput(reply_text=reply_text, a2ui_messages=[], session=session)
        return TurnOutput(
            reply_text=f"⚠️ Could not exchange Microsoft authorization code: `{ex_res.get('error')}`. Please click the sign-in link again to generate a fresh code.",
            a2ui_messages=[],
            session=session,
        )

    # Case B: User asks to save/sync to SharePoint or confirms they just logged in
    is_save_or_login = any(
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
            "logged in",
            "signed in",
        )
    ) or (
        "sharepoint" in text_lower
        and any(w in text_lower for w in ("save", "sync", "write", "push", "upload", "login", "sign in", "connect"))
    )

    if is_save_or_login:
        from qualify.connectors.sharepoint import sync_to_optional_sharepoint  # noqa: PLC0415

        sp_res = sync_to_optional_sharepoint(
            session.record,
            skipped_stages=session.skipped,
            context_id=session.context_id,
        )
        if sp_res and sp_res.auth_mode == "delegated":
            title = session.record.meta.initiative_name or session.record.meta.record_id or "Opportunity"
            reply_text = (
                f"✅ **Successfully Saved to SharePoint Online (On-Behalf-Of User)!**\n\n"
                f"- **Initiative**: {title} (`{session.record.meta.record_id}`)\n"
                f"- **SharePoint Folder**: [Open Opportunity Folder in SharePoint]({sp_res.folder_url})\n"
                f"- **Business Value Brief**: [View Business_Value_Brief.md]({sp_res.brief_url})\n\n"
                + render_business_brief(session.record, skipped_stages=session.skipped)
            )
            return TurnOutput(
                reply_text=reply_text,
                a2ui_messages=[],
                session=session,
            )

        base_url = _os.environ.get("AGENT_URL", "https://ge-qualify-agent-g22bhpwccq-uc.a.run.app").rstrip("/")
        auth_link = f"{base_url}/auth?context_id={_up.quote(session.context_id)}"
        tenant_id = _os.environ.get("MS_GRAPH_TENANT_ID", "918002ad-54bb-4139-804a-2da0d762bd54").strip()
        client_id = _os.environ.get("MS_GRAPH_CLIENT_ID", "d8a18018-c2a9-4f7e-b464-320141d6623d").strip()
        ms_direct_auth = (
            f"https://login.microsoftonline.com/{_up.quote(tenant_id)}/oauth2/v2.0/authorize?"
            + _up.urlencode({
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": "https://vertexaisearch.cloud.google.com/oauth-redirect",
                "response_mode": "query",
                "scope": "https://graph.microsoft.com/Sites.ReadWrite.All offline_access",
                "state": session.context_id,
                "prompt": "select_account",
            })
        )
        reply_text = (
            f"🔐 **Microsoft SharePoint User Sign-In Required**\n\n"
            f"To save **{session.record.meta.initiative_name or session.record.meta.record_id}** directly under your Microsoft account (without Azure Device Code restrictions):\n\n"
            f"1. **[Click here to Sign In with Microsoft ↗]({ms_direct_auth})** *(uses your registered `vertexaisearch` Redirect URI)*\n"
            f"2. After signing in, **copy the URL from your browser's address bar** (`https://vertexaisearch.cloud.google.com/oauth-redirect?code=...`) and **paste it right here in chat** (or paste it on the **[SharePoint Auth Page]({auth_link})**).\n\n"
            f"As soon as you paste that URL here, I will immediately exchange your token and save this opportunity to SharePoint Online!"
        )
        return TurnOutput(
            reply_text=reply_text,
            a2ui_messages=[],
            session=session,
        )

    # Match explicit SharePoint load/open requests or direct Record ID load
    has_sp_keyword = "sharepoint" in text_lower or "load uc-" in text_lower or "open uc-" in text_lower
    if not has_sp_keyword:
        return None

    from qualify.connectors.sharepoint import get_sharepoint_connector  # noqa: PLC0415

    # Check if user wants to list / search all SharePoint opportunities
    if any(k in text_lower for k in ("list sharepoint", "search sharepoint", "show sharepoint", "sharepoint opportunities")):
        connector = get_sharepoint_connector()
        items = connector.search_opportunities("")
        if not items:
            return TurnOutput(
                reply_text=" Connected to SharePoint Online (`https://zd8vn.sharepoint.com/`), but no qualification opportunities were found yet. Complete Stage 4 and click **Submit** to create your first opportunity!",
                a2ui_messages=[],
                session=session,
            )
        lines = ["### 📂 Qualification Opportunities in SharePoint Online\n"]
        for item in items:
            rec_id = item.get("recordId") or item.get("Title") or "Unknown ID"
            name = item.get("initiativeName") or item.get("name") or rec_id
            url = item.get("webUrl") or "https://zd8vn.sharepoint.com/"
            lines.append(f"- **{name}** (`{rec_id}`) — [Open in SharePoint]({url}) *(Type `load {rec_id} from sharepoint` to open)*")
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

    connector = get_sharepoint_connector()
    loaded = connector.load_opportunity(query)
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
        + render_business_brief(loaded, skipped_stages=session.skipped)
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

