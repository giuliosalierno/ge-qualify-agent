"""Routing an inbound A2UI action to the code that handles it.

This is the only way user input reaches the record. `sendDataModel` is a
no-op in Gemini Enterprise (L1) — `message.metadata` is null on every turn —
so there is no passive channel to fall back on. If the routing here is wrong,
the interview is silently read-only.

The envelope GE sends was captured first-hand during the L12 probe::

    {
      "name": "spike_select_submit",
      "sourceComponentId": "submit",
      "surfaceId": "l12-select-probe",
      "timestamp": "2026-09-15T14:14:56.025Z",
      "context": {"dropdown": "wb_custom_mcp", "radio": "high_code_agent", ...}
    }

`context` holds whatever the button's action bound. Ours binds `{"path":
"/uc"}`, which resolves to the whole record subtree.

**Unknown action names are logged and ignored.** A2UI is pre-1.0 (L9) and the
agent card is frozen at registration (L2), so a renderer sending an event we
have never heard of is a question of when. Raising would turn a cosmetic
mismatch into a dead conversation; the user would lose an interview because a
button we do not use fired.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from qualify.a2ui.provenance import CommitResult, apply_commit
from qualify.packs.loader import Pack
from qualify.sinks.session import Session

log = logging.getLogger(__name__)

COMMIT_STAGE = "commit_stage"
SKIP_STAGE = "skip_stage"
REVISE_STAGE = "revise_stage"
REQUEST_GUIDANCE = "request_guidance"
FINALIZE = "finalize"
ATTACH_DOCUMENT = "attach_document"

KNOWN_ACTIONS = frozenset(
    {COMMIT_STAGE, SKIP_STAGE, REVISE_STAGE, REQUEST_GUIDANCE, FINALIZE, ATTACH_DOCUMENT}
)


@dataclass
class ActionEvent:
    """A parsed inbound action.

    Parsed into a dataclass rather than passed around as a dict so that a
    renamed key fails at the boundary, once, instead of surfacing three calls
    later as a `None`.
    """

    name: str
    context: dict[str, Any] = field(default_factory=dict)
    source_component_id: str | None = None
    surface_id: str | None = None
    timestamp: str | None = None

    @property
    def is_known(self) -> bool:
        return self.name in KNOWN_ACTIONS


@dataclass
class ActionOutcome:
    """What the router did, for the agent to narrate.

    Carries no prose beyond `message`. Wording belongs in the agent
    instruction, where it can adapt to the conversation; this layer decides
    only what happened and whether it worked.
    """

    action: str
    handled: bool
    #: True when the interview may move on.
    advanced: bool = False
    #: True when the stage was skipped rather than fully confirmed.
    skipped: bool = False
    #: Set when the action changed which stage is active.
    stage: str | None = None
    commit: CommitResult | None = None
    #: Stage index and surfaceId of the stage just committed, so its form card
    #: can be collapsed into a 1-line summary banner.
    committed_stage_idx: int | None = None
    committed_surface_id: str | None = None
    #: A short factual note. Not user-facing copy.
    message: str = ""
    #: Set when the whole pack is committed and `finalize` may run.
    ready_to_finalize: bool = False


def parse_action(raw: dict[str, Any]) -> ActionEvent | None:
    """Reads GE's action envelope.

    Returns None when `raw` is not an action at all, which is the common case:
    most turns are plain chat. Distinguishing "not an action" from "an action
    I cannot handle" matters, because only the second is worth logging.
    """
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None

    context = raw.get("context")
    if not isinstance(context, dict):
        context = {}

    return ActionEvent(
        name=name,
        context=context,
        source_component_id=raw.get("sourceComponentId"),
        surface_id=raw.get("surfaceId"),
        timestamp=raw.get("timestamp"),
    )


def dispatch(session: Session, event: ActionEvent) -> ActionOutcome:
    """Applies one action to one session.

    Returns rather than raises for every outcome a user can cause. An
    incomplete stage is a normal state to be talked through, not an
    exception — see `CommitResult`.
    """
    if event.name == COMMIT_STAGE:
        return _commit_stage(session, event)
    if event.name == SKIP_STAGE:
        return _skip_stage(session, event)
    if event.name == REVISE_STAGE:
        return _revise_stage(session, event)
    if event.name == REQUEST_GUIDANCE:
        return _request_guidance(session, event)
    if event.name == FINALIZE:
        return _finalize(session, event)
    if event.name == ATTACH_DOCUMENT:
        return _attach_document(session, event)

    # Never raise. See the module docstring.
    log.warning(
        "Ignoring unknown A2UI action %r from component %r on surface %r",
        event.name,
        event.source_component_id,
        event.surface_id,
    )
    return ActionOutcome(
        action=event.name,
        handled=False,
        message=f"unknown action {event.name!r}, ignored",
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _commit_stage(session: Session, event: ActionEvent) -> ActionOutcome:
    """Writes the visible stage into the record and decides whether to move on.

    The stage index comes from the session, not from the payload. A client
    could send any `stage` it liked, and trusting it would let a malformed or
    replayed event commit stage 3's validation rules against stage 1's data.
    The payload's `stage`, if present, is treated as a claim to check.
    """
    pack: Pack = session.pack
    stage_idx = session.active_stage

    claimed = event.context.get("stage")
    if isinstance(claimed, str) and claimed != pack.stages[stage_idx].id:
        # Usually a stale surface: the user pressed Continue on a card from
        # earlier in the scrollback. Honouring it would overwrite newer
        # answers with older ones.
        log.warning(
            "commit_stage claimed stage %r but session is on %r; ignoring",
            claimed,
            pack.stages[stage_idx].id,
        )
        return ActionOutcome(
            action=COMMIT_STAGE,
            handled=False,
            stage=pack.stages[stage_idx].id,
            message=f"stale commit for {claimed!r}, session is on "
            f"{pack.stages[stage_idx].id!r}",
        )

    payload = _record_payload(event.context)
    result = apply_commit(session.record, pack, stage_idx, payload)

    if not result.ok:
        return ActionOutcome(
            action=COMMIT_STAGE,
            handled=True,
            advanced=False,
            stage=result.stage_id,
            commit=result,
            message=result.blocking_summary(),
        )

    committed_idx = stage_idx
    committed_sid = event.surface_id or session.stage_surface_ids.get(stage_idx)
    moved = session.advance()
    return ActionOutcome(
        action=COMMIT_STAGE,
        handled=True,
        advanced=True,
        stage=session.stage,
        commit=result,
        committed_stage_idx=committed_idx,
        committed_surface_id=committed_sid,
        ready_to_finalize=session.is_complete,
        message=(
            f"committed {result.stage_id}, now on {session.stage}"
            if moved
            else f"committed {result.stage_id}, all stages done"
        ),
    )


def _skip_stage(session: Session, event: ActionEvent) -> ActionOutcome:
    """Skips the active stage (allowed for stage_idx > 0), saving any partial inputs."""
    pack: Pack = session.pack
    stage_idx = session.active_stage

    if stage_idx == 0:
        return ActionOutcome(
            action=SKIP_STAGE,
            handled=True,
            advanced=False,
            stage=pack.stages[0].id,
            message="Stage 1 (The problem) cannot be skipped because every use case requires at least an initiative name and problem description.",
        )

    payload = _record_payload(event.context)
    # Save any partial valid inputs without blocking on missing required fields
    result = apply_commit(session.record, pack, stage_idx, payload)

    committed_idx = stage_idx
    committed_sid = event.surface_id or session.stage_surface_ids.get(stage_idx)
    moved = session.skip_active_stage()
    return ActionOutcome(
        action=SKIP_STAGE,
        handled=True,
        advanced=True,
        skipped=True,
        stage=session.stage,
        commit=result,
        committed_stage_idx=committed_idx,
        committed_surface_id=committed_sid,
        ready_to_finalize=session.is_complete,
        message=(
            f"skipped {result.stage_id}, now on {session.stage}"
            if moved
            else f"skipped {result.stage_id}, all stages done"
        ),
    )


def _revise_stage(session: Session, event: ActionEvent) -> ActionOutcome:
    """Reopens a stage the user wants to change.

    Accepts a stage id rather than an index. Indices are an implementation
    detail of the pack ordering, and a surface built before a pack edit would
    point at the wrong stage.
    """
    target = event.context.get("stage")
    if not isinstance(target, str):
        return ActionOutcome(
            action=REVISE_STAGE,
            handled=False,
            message="revise_stage needs a stage id in its context",
        )

    pack = session.pack
    for idx, stage in enumerate(pack.stages):
        if stage.id == target:
            session.reopen(idx)
            return ActionOutcome(
                action=REVISE_STAGE,
                handled=True,
                stage=target,
                message=f"reopened {target}",
            )

    log.warning("revise_stage named unknown stage %r", target)
    return ActionOutcome(
        action=REVISE_STAGE,
        handled=False,
        message=f"no stage {target!r} in pack {session.pack_name!r}",
    )


def _request_guidance(session: Session, event: ActionEvent) -> ActionOutcome:
    """Asks the agent to explain a field. Explicitly changes no state.

    Handled here anyway so that pressing Help cannot be mistaken for an
    unknown action and logged as a warning on every press.
    """
    path = event.context.get("path")
    return ActionOutcome(
        action=REQUEST_GUIDANCE,
        handled=True,
        stage=session.stage,
        message=f"guidance requested for {path!r}",
    )


def _finalize(session: Session, event: ActionEvent) -> ActionOutcome:
    """Closes the interview, if every stage is committed.

    The completeness check lives here rather than in the button, because the
    button cannot be trusted to be disabled — the base `Button` has no
    `disabled` prop (L11).

    Persisting to the Sheet and GCS is Phase 3. This reports readiness only,
    so a half-finished record cannot reach a sink that does not exist yet.
    """
    if not session.is_complete:
        pack = session.pack
        outstanding = [
            s.id for i, s in enumerate(pack.stages) if i not in session.committed
        ]
        return ActionOutcome(
            action=FINALIZE,
            handled=True,
            advanced=False,
            stage=session.stage,
            message=f"not finished: {', '.join(outstanding)} still open",
        )

    return ActionOutcome(
        action=FINALIZE,
        handled=True,
        advanced=True,
        stage=session.stage,
        ready_to_finalize=True,
        message="all stages committed",
    )


def _attach_document(session: Session, event: ActionEvent) -> ActionOutcome:
    """Phase 4, and possibly never in this form.

    There is no `FileUpload` in the 52-component GE catalog (L10), so nothing
    can fire this today. Kept as a named no-op so the eventual arrival of one
    is a small change here rather than a new branch in the router.
    """
    log.info("attach_document received but not implemented; ignoring")
    return ActionOutcome(
        action=ATTACH_DOCUMENT,
        handled=False,
        stage=session.stage,
        message="document attachment is not implemented yet",
    )


# ---------------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------------


def _record_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Finds the record subtree inside the action context.

    The button binds `{"path": "/uc"}`, so GE resolves that and the record
    sections arrive as the context's own top-level keys. But the probe showed
    GE also adds its own entries — `prompt` among them — so the context is not
    purely ours.

    Passing it through whole is safe: `extract_stage_values` reads only the
    paths the pack declares, and a stray `prompt` key matches none of them.
    The one shape worth normalising is an explicit `uc` or `data` wrapper,
    which a differently bound button would produce.
    """
    for wrapper in ("uc", "data"):
        inner = context.get(wrapper)
        if isinstance(inner, dict):
            return inner
    return context
