"""Tests for the action router and the session store.

Two behaviours carry most of the weight here, and both are about refusing to
trust the client:

* the stage index comes from the session, never from the payload;
* an action name we do not recognise is logged and ignored, never raised.

The second is not defensive coding for its own sake. A2UI is pre-1.0 and the
agent card is frozen at registration, so a renderer sending an unfamiliar
event is a matter of time. Raising would cost the user a whole interview
because a button we do not use fired.
"""

from __future__ import annotations

import logging

import pytest

from qualify.a2ui.actions import (
    ATTACH_DOCUMENT,
    COMMIT_STAGE,
    FINALIZE,
    REQUEST_GUIDANCE,
    REVISE_STAGE,
    ActionEvent,
    dispatch,
    parse_action,
)
from qualify.sinks.session import (
    InMemorySessionStore,
    Session,
    get_or_start,
    new_session,
)

# Captured verbatim from the L12 probe. If GE ever changes the envelope, this
# is the fixture that should fail first.
REAL_ENVELOPE = {
    "context": {
        "all": {},
        "chips": "low_code",
        "dropdown": "wb_custom_mcp",
        "prompt": "Submit the select probe",
        "radio": "high_code_agent",
        "toggle": "pro_code",
    },
    "name": "spike_select_submit",
    "sourceComponentId": "submit",
    "surfaceId": "l12-select-probe",
    "timestamp": "2026-09-15T14:14:56.025Z",
}

NEEDS_DATA = {
    "meta": {"initiative_name": "Claims triage"},
    "business": {
        "problem_description": "Handlers re-key claims by hand.",
        "user_profile": "Claims handler",
        "user_count": "12",
    },
}

SIZING_DATA = {
    "sizing": {
        "task_frequency_weekly": "5",
        "baseline_minutes_per_task": "20",
        "target_minutes_saved_per_task": "15",
    }
}

DATA_DATA = {
    "technical": {
        "data_sources": ["sharepoint"],
        "security": {"data_classification": "internal"},
    }
}

OWNERSHIP_DATA = {"meta": {"submitter": "Ana Ruiz"}}

ALL_STAGES = [NEEDS_DATA, SIZING_DATA, DATA_DATA, OWNERSHIP_DATA]


@pytest.fixture
def session() -> Session:
    return new_session("ctx-test")


def commit(session: Session, data: dict) -> object:
    return dispatch(session, ActionEvent(name=COMMIT_STAGE, context=data))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_the_real_envelope() -> None:
    event = parse_action(REAL_ENVELOPE)

    assert event is not None
    assert event.name == "spike_select_submit"
    assert event.source_component_id == "submit"
    assert event.surface_id == "l12-select-probe"
    assert event.context["chips"] == "low_code"
    assert not event.is_known


@pytest.mark.parametrize(
    "raw", [{}, {"name": ""}, {"context": {}}, "not a dict", None]
)
def test_non_actions_parse_to_none(raw: object) -> None:
    """Most turns are plain chat. "Not an action" must be cheap and quiet —
    only "an action I cannot handle" is worth logging."""
    assert parse_action(raw) is None  # type: ignore[arg-type]


def test_missing_context_becomes_an_empty_dict() -> None:
    """So handlers can use `.get` without a None check at every call site."""
    event = parse_action({"name": "commit_stage"})
    assert event is not None
    assert event.context == {}


# ---------------------------------------------------------------------------
# Unknown actions
# ---------------------------------------------------------------------------


def test_unknown_action_is_ignored_not_raised(session, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        outcome = dispatch(session, ActionEvent(name="teleport"))

    assert outcome.handled is False
    assert outcome.advanced is False
    assert "teleport" in caplog.text
    # And the session is untouched.
    assert session.active_stage == 0


# ---------------------------------------------------------------------------
# commit_stage
# ---------------------------------------------------------------------------


def test_commit_advances_the_stage(session) -> None:
    outcome = commit(session, NEEDS_DATA)

    assert outcome.handled and outcome.advanced
    assert outcome.stage == "sizing"
    assert session.active_stage == 1
    assert 0 in session.committed


def test_incomplete_commit_does_not_advance(session) -> None:
    payload = {"business": {"user_profile": "Claims handler"}}

    outcome = commit(session, payload)

    assert outcome.handled  # it ran; it just did not pass
    assert not outcome.advanced
    assert session.active_stage == 0
    assert "still needed" in outcome.message


def test_a_stale_card_cannot_commit_the_wrong_stage(session) -> None:
    """The user scrolls up and presses Continue on stage 1's card while the
    session is on stage 2. Honouring it would validate stage 2's rules
    against stage 1's data and overwrite newer answers with older ones."""
    commit(session, NEEDS_DATA)
    assert session.active_stage == 1

    stale = dict(NEEDS_DATA, stage="needs")
    outcome = dispatch(session, ActionEvent(name=COMMIT_STAGE, context=stale))

    assert not outcome.handled
    assert session.active_stage == 1
    assert "stale" in outcome.message


def test_a_matching_stage_claim_is_accepted(session) -> None:
    """The check rejects mismatches, not the presence of the field."""
    payload = dict(NEEDS_DATA, stage="needs")
    outcome = dispatch(session, ActionEvent(name=COMMIT_STAGE, context=payload))

    assert outcome.advanced


def test_ge_extra_context_keys_are_harmless(session) -> None:
    """GE adds its own entries to the context — `prompt` was observed in the
    probe. Extraction reads only the paths the pack declares, so they cannot
    collide."""
    payload = dict(NEEDS_DATA, prompt="Continue", surfaceId="qualify")

    outcome = dispatch(session, ActionEvent(name=COMMIT_STAGE, context=payload))

    assert outcome.advanced


def test_an_explicit_uc_wrapper_is_unwrapped(session) -> None:
    outcome = dispatch(
        session, ActionEvent(name=COMMIT_STAGE, context={"uc": NEEDS_DATA})
    )
    assert outcome.advanced


def test_committing_the_last_stage_reports_readiness(session) -> None:
    for data in ALL_STAGES:
        outcome = commit(session, data)
        assert outcome.handled, outcome.message

    assert outcome.ready_to_finalize
    assert session.is_complete


# ---------------------------------------------------------------------------
# revise_stage
# ---------------------------------------------------------------------------


def test_revise_reopens_an_earlier_stage(session) -> None:
    commit(session, NEEDS_DATA)
    commit(session, SIZING_DATA)
    assert session.active_stage == 2

    outcome = dispatch(
        session, ActionEvent(name=REVISE_STAGE, context={"stage": "needs"})
    )

    assert outcome.handled
    assert session.active_stage == 0
    assert 0 not in session.committed


def test_revise_keeps_later_stages_committed(session) -> None:
    """A user fixing a typo in stage 1 should not have to re-confirm stages 2
    and 3. Field-level `user_confirmed` marks are what protect their data."""
    commit(session, NEEDS_DATA)
    commit(session, SIZING_DATA)

    dispatch(session, ActionEvent(name=REVISE_STAGE, context={"stage": "needs"}))

    assert 1 in session.committed


def test_revise_with_an_unknown_stage_is_refused(session, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        outcome = dispatch(
            session, ActionEvent(name=REVISE_STAGE, context={"stage": "nope"})
        )

    assert not outcome.handled
    assert session.active_stage == 0


def test_revise_without_a_stage_is_refused(session) -> None:
    outcome = dispatch(session, ActionEvent(name=REVISE_STAGE))
    assert not outcome.handled


# ---------------------------------------------------------------------------
# request_guidance and finalize
# ---------------------------------------------------------------------------


def test_guidance_changes_nothing(session) -> None:
    outcome = dispatch(
        session,
        ActionEvent(
            name=REQUEST_GUIDANCE, context={"path": "/uc/business/user_count"}
        ),
    )

    assert outcome.handled
    assert not outcome.advanced
    assert session.active_stage == 0


def test_finalize_refuses_an_incomplete_interview(session) -> None:
    """Server-side because it has to be: the base `Button` has no `disabled`
    prop, so nothing stops the user pressing this early."""
    commit(session, NEEDS_DATA)

    outcome = dispatch(session, ActionEvent(name=FINALIZE))

    assert outcome.handled
    assert not outcome.ready_to_finalize
    assert "sizing" in outcome.message


def test_finalize_accepts_a_complete_interview(session) -> None:
    for data in ALL_STAGES:
        commit(session, data)

    outcome = dispatch(session, ActionEvent(name=FINALIZE))

    assert outcome.ready_to_finalize


def test_attach_document_is_a_known_no_op(session, caplog) -> None:
    """There is no FileUpload in the GE catalog, so nothing can fire this.
    It stays named so it is not logged as an unknown action."""
    outcome = dispatch(session, ActionEvent(name=ATTACH_DOCUMENT))

    assert not outcome.handled
    assert "not implemented" in outcome.message
    assert "unknown" not in caplog.text.lower()


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------


def test_context_id_is_written_into_the_record() -> None:
    """So a record recovered from the sink alone can be traced back to its
    conversation."""
    s = new_session("ctx-42")
    assert s.record.meta.context_id == "ctx-42"


def test_get_or_start_is_idempotent() -> None:
    store = InMemorySessionStore(quiet=True)

    first = get_or_start(store, "ctx-1")
    first.record.business.user_profile = "Claims handler"
    second = get_or_start(store, "ctx-1")

    assert second is first
    assert second.record.business.user_profile == "Claims handler"
    assert len(store) == 1


def test_different_conversations_do_not_share_state() -> None:
    store = InMemorySessionStore(quiet=True)

    a = get_or_start(store, "ctx-a")
    b = get_or_start(store, "ctx-b")
    a.record.business.user_profile = "Claims handler"

    assert b.record.business.user_profile is None
    assert len(store) == 2


def test_a_bad_pack_name_fails_before_any_state_exists() -> None:
    from qualify.packs.loader import PackError

    with pytest.raises(PackError):
        new_session("ctx-x", pack_name="no-such-pack")


def test_advance_returns_false_on_the_last_stage() -> None:
    s = new_session("ctx-end")
    s.active_stage = len(s.pack.stages) - 1

    assert s.advance() is False
    assert s.is_complete is False  # only the last stage was committed


def test_reopen_rejects_an_out_of_range_stage() -> None:
    s = new_session("ctx-oob")
    with pytest.raises(IndexError):
        s.reopen(99)


def test_save_updates_the_timestamp() -> None:
    store = InMemorySessionStore(quiet=True)
    s = new_session("ctx-t")
    before = s.updated_at

    store.save(s)

    assert s.updated_at >= before


def test_delete_removes_the_session() -> None:
    store = InMemorySessionStore(quiet=True)
    store.save(new_session("ctx-d"))

    store.delete("ctx-d")

    assert store.load("ctx-d") is None
    # Deleting twice is not an error; a retried turn should not crash.
    store.delete("ctx-d")


def test_in_memory_store_warns_about_l13(caplog) -> None:
    """L13 is a known open point, not a bug, but its failure mode is silent:
    a second Cloud Run instance loses the interview and the logs look like a
    normal new conversation.

    This warning is the only signal that exists, so it is pinned here to stop
    a future tidy-up removing it.
    """
    with caplog.at_level(logging.WARNING):
        InMemorySessionStore()

    assert "max-instances=1" in caplog.text
    assert "L13" in caplog.text

