"""Tests for the Phase 1 to Phase 2 handover.

The handover has one job: turn a record id typed in a fresh conversation into a
technical review of the right initiative. Every failure mode here is silent if
it is not tested — a wrong record, an empty record, or a review that reverts to
business halfway through all *look* like working software.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.agent.handover import (
    HandoverError,
    baseline_summary,
    parse_tech_review_intent,
    start_tech_review,
)
from qualify.agent.turn import TurnInput, execute_turn
from qualify.schema.capability import CapabilityLevel
from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import InMemorySessionStore, new_session


@pytest.fixture
def store(tmp_path: Path) -> LocalRecordStore:
    return LocalRecordStore(tmp_path)


@pytest.fixture
def saved_record_id(store: LocalRecordStore) -> str:
    """A completed-enough business record, persisted and ready to hand over."""
    session = new_session("ctx-phase1", pack_name="business", record_id="UC-2026-ABC123")
    session.record.meta.initiative_name = "Claims triage assistant"
    session.record.meta.department_bu = "Claims Operations"
    session.record.business.problem_description = "Adjusters rekey claims by hand."
    session.record.business.user_count = 40
    session.record.technical.data_sources = ["sharepoint", "salesforce"]
    session.record.technical.capability_level = CapabilityLevel.HIGH_CODE_AGENT
    session.committed.update({0, 1, 2, 3})
    store.save(session)
    return "UC-2026-ABC123"


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_id",
    [
        ("technical review UC-2026-ABC123", "UC-2026-ABC123"),
        ("start the tech review for UC-2026-ABC123 please", "UC-2026-ABC123"),
        ("Technical Architecture Review of uc-2026-abc123", "UC-2026-ABC123"),
        ("start phase 2 on UC-2026-ZZ9", "UC-2026-ZZ9"),
        ("technical review", None),
    ],
)
def test_parse_extracts_record_id(text: str, expected_id: str | None) -> None:
    wants, record_id = parse_tech_review_intent(text)
    assert wants is True
    assert record_id == expected_id


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "what systems does this touch?",
        # Contains a record id but asks for nothing — must not hijack the turn.
        "the brief for UC-2026-ABC123 looks good",
        # "review" alone is ordinary conversation.
        "can you review my answers?",
    ],
)
def test_parse_ignores_ordinary_conversation(text: str | None) -> None:
    wants, record_id = parse_tech_review_intent(text)
    assert wants is False
    assert record_id is None


# ---------------------------------------------------------------------------
# Opening the review
# ---------------------------------------------------------------------------


def test_start_tech_review_opens_the_saved_record(
    store: LocalRecordStore, saved_record_id: str
) -> None:
    session = start_tech_review(store, "ctx-phase2", saved_record_id)

    assert session.pack_name == "tech"
    assert session.context_id == "ctx-phase2"
    # The point of the handover: Phase 1's answers came across intact.
    assert session.record.meta.record_id == saved_record_id
    assert session.record.meta.initiative_name == "Claims triage assistant"
    assert session.record.business.user_count == 40
    # And the review starts at its own first stage, not Phase 1's last.
    assert session.active_stage == 0
    assert session.committed == set()


def test_start_tech_review_persists_under_the_new_context(
    store: LocalRecordStore, saved_record_id: str
) -> None:
    start_tech_review(store, "ctx-phase2", saved_record_id)

    reloaded = store.load("ctx-phase2")
    assert reloaded is not None
    assert reloaded.pack_name == "tech"


def test_unknown_record_id_is_refused(store: LocalRecordStore) -> None:
    with pytest.raises(HandoverError, match="UC-2026-NOPE"):
        start_tech_review(store, "ctx-phase2", "UC-2026-NOPE")


def test_in_memory_store_is_refused_with_an_actionable_message() -> None:
    """The deployment gap has to surface as advice, not as an empty review.

    An in-memory store cannot reach a record from an earlier conversation. If
    that returned an empty session instead of raising, the reviewer would
    qualify a blank initiative and only discover it at the SharePoint write.
    """
    with pytest.raises(HandoverError, match="QUALIFY_GCS_BUCKET"):
        start_tech_review(InMemorySessionStore(quiet=True), "ctx", "UC-2026-ABC123")


def test_baseline_summary_reports_only_what_phase_1_recorded(
    store: LocalRecordStore, saved_record_id: str
) -> None:
    session = start_tech_review(store, "ctx-phase2", saved_record_id)
    summary = baseline_summary(session)

    assert "Claims triage assistant" in summary
    assert "UC-2026-ABC123" in summary
    assert "Claims Operations" in summary
    assert "40" in summary


def test_baseline_summary_omits_fields_phase_1_left_empty(
    store: LocalRecordStore,
) -> None:
    session = new_session("ctx-sparse", pack_name="business", record_id="UC-2026-SPARSE")
    store.save(session)
    handed = start_tech_review(store, "ctx-phase2", "UC-2026-SPARSE")

    summary = baseline_summary(handed)
    # No invented values. Zero Extrapolation Rule.
    assert "Team:" not in summary
    assert "Affected users:" not in summary
    assert "Unnamed initiative" in summary


# ---------------------------------------------------------------------------
# End to end through execute_turn
# ---------------------------------------------------------------------------


def test_chat_command_switches_the_conversation_to_the_tech_pack(
    store: LocalRecordStore, saved_record_id: str
) -> None:
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text=f"technical review {saved_record_id}"),
    )

    assert out.session.pack_name == "tech"
    assert "Claims triage assistant" in out.reply_text
    assert out.a2ui_messages, "the first tech stage should render a surface"


def test_handover_survives_the_rest_of_the_turn(
    store: LocalRecordStore,
    saved_record_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards the `store.save(output.session)` fix in `execute_turn`.

    `get_or_start` creates a business session before the handover replaces it.
    Saving the pre-turn object at the end of `execute_turn` would silently roll
    the handover back, and the next turn would be a business interview again —
    with the reviewer's first answers already lost.

    A connected SharePoint token is required to make this bite: the trailing
    save only runs when the sign-in banner fires. Without the token this test
    passes whether or not the bug is present, which is worth stating out loud —
    it was written that way first, and a mutation run caught it.
    """
    import qualify.connectors.token_vault as token_vault

    monkeypatch.setattr(token_vault, "get_access", lambda _ctx: "fake-token")

    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text=f"tech review {saved_record_id}"),
    )
    # The banner really did fire, so the trailing save really did run.
    assert "SharePoint connected" in out.reply_text

    persisted = store.load("ctx-reviewer")
    assert persisted is not None
    assert persisted.pack_name == "tech"

    # And the turn after it stays in the technical review.
    nxt = execute_turn(
        store, TurnInput(context_id="ctx-reviewer", user_text="the data lives in SAP")
    )
    assert nxt.session.pack_name == "tech"


def test_request_without_an_id_asks_for_one(store: LocalRecordStore) -> None:
    out = execute_turn(
        store, TurnInput(context_id="ctx-reviewer", user_text="start a technical review")
    )

    assert "record id" in out.reply_text.lower()
    # Nothing was started, so the conversation is still a business intake.
    assert out.session.pack_name == "business"


def test_unknown_id_explains_itself_without_starting_a_review(
    store: LocalRecordStore,
) -> None:
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text="technical review UC-2026-GHOST"),
    )

    assert "UC-2026-GHOST" in out.reply_text
    assert out.session.pack_name == "business"
