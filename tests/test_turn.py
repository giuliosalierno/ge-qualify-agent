"""Tests for the end-to-end turn loop.

Verifies:
- Turn 1 emits the initial A2UI surface for Stage 1.
- Conversational chat extracts field drafts and emits incremental A2UI patches.
- Commits are gated server-side: incomplete stages are blocked with clear feedback.
- Completed stages advance and render the next stage's surface.
- Reopening a stage re-renders that stage's surface.
- Full 4-stage business intake walkthrough reaches completion.
"""

from __future__ import annotations

from typing import Any

import pytest

from qualify.agent.turn import (
    TurnInput,
    execute_turn,
)
from qualify.a2ui.actions import COMMIT_STAGE, REVISE_STAGE
from qualify.sinks.session import InMemorySessionStore

NEEDS_PAYLOAD = {
    "meta": {"initiative_name": "Claims triage"},
    "business": {
        "problem_description": "Handlers re-key claims by hand from email.",
        "user_profile": "Claims handler",
        "user_count": "12",
    },
}

SIZING_PAYLOAD = {
    "sizing": {
        "task_frequency_weekly": "5",
        "baseline_minutes_per_task": "20",
        "target_minutes_saved_per_task": "15",
    }
}

DATA_PAYLOAD = {
    "technical": {
        "data_sources": ["sharepoint"],
        "security": {"data_classification": "internal"},
    }
}

OWNERSHIP_PAYLOAD = {"meta": {"submitter": "Ana Ruiz"}}


class MockExtractionClient:
    """Returns predetermined field drafts."""

    def __init__(self, drafts: list[dict[str, Any]]) -> None:
        self.drafts = drafts

    def propose(self, **_: Any) -> list[dict[str, Any]]:
        return self.drafts


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore(quiet=True)


def test_turn_1_initializes_surface_and_session(store: InMemorySessionStore) -> None:
    inp = TurnInput(
        context_id="ctx-001",
        user_text="Claims triage",
        conversation_history="User: Claims triage",
    )
    out = execute_turn(store, inp)

    assert out.session.context_id == "ctx-001"
    assert out.session.active_stage == 0
    assert 0 in out.session.rendered_stages
    # Initial surface messages: createSurface, updateComponents, updateDataModel
    assert len(out.a2ui_messages) == 3
    assert "createSurface" in out.a2ui_messages[0]
    assert "updateComponents" in out.a2ui_messages[1]
    assert "updateDataModel" in out.a2ui_messages[2]
    assert "Problem and users" in out.reply_text


def test_chat_turn_emits_patches_for_extracted_fields(store: InMemorySessionStore) -> None:
    # First turn sets up the surface
    execute_turn(store, TurnInput(context_id="ctx-002", user_text="Hello"))

    # Second turn user provides user count
    extractor = MockExtractionClient(
        [
            {
                "path": "/uc/business/user_count",
                "value": "12",
                "evidence": "12 handlers",
            }
        ]
    )
    inp = TurnInput(
        context_id="ctx-002",
        user_text="We have 12 handlers on the team.",
        conversation_history="User: Hello\nAssistant: Hi\nUser: We have 12 handlers on the team.",
    )
    out = execute_turn(store, inp, extraction_client=extractor)

    # Bare patch emitted, not a full rebuild
    assert len(out.drafts) == 1
    assert out.drafts[0].path == "/uc/business/user_count"
    assert out.drafts[0].value == 12
    assert any(
        msg.get("updateDataModel", {}).get("path") == "/uc/business/user_count"
        for msg in out.a2ui_messages
    )
    # The record got updated with agent_draft
    assert out.session.record.business.user_count == 12
    assert out.session.record.provenance_of("business.user_count") == "agent_draft"


def test_incomplete_commit_is_blocked_and_explains_missing(store: InMemorySessionStore) -> None:
    execute_turn(store, TurnInput(context_id="ctx-003", user_text="Start"))

    # User clicks Continue with only partial data
    action_turn = TurnInput(
        context_id="ctx-003",
        action_data={
            "name": COMMIT_STAGE,
            "context": {"business": {"user_count": "12"}},
        },
    )
    out = execute_turn(store, action_turn)

    assert out.outcome is not None
    assert not out.outcome.advanced
    assert out.session.active_stage == 0
    # No new surface rendered
    assert len(out.a2ui_messages) == 0
    assert "still needed" in out.reply_text
    assert "Who does this work" in out.reply_text


def test_complete_commit_advances_and_renders_next_stage(store: InMemorySessionStore) -> None:
    execute_turn(store, TurnInput(context_id="ctx-004", user_text="Start"))

    # Commit stage 0 completely
    action_turn = TurnInput(
        context_id="ctx-004",
        action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD},
    )
    out = execute_turn(store, action_turn)

    assert out.outcome is not None
    assert out.outcome.advanced
    assert out.session.active_stage == 1
    assert out.session.stage == "sizing"
    # New surface rendered for stage 1 (3 messages, single surfaceId)
    assert len(out.a2ui_messages) == 3
    assert "createSurface" in out.a2ui_messages[0]
    assert "updateComponents" in out.a2ui_messages[1]
    assert "Effort and value" in out.reply_text


def test_reopen_stage_re_renders_surface(store: InMemorySessionStore) -> None:
    # Advance to stage 1
    execute_turn(store, TurnInput(context_id="ctx-005", user_text="Start"))
    execute_turn(
        store,
        TurnInput(
            context_id="ctx-005",
            action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD},
        ),
    )

    # Now revise stage 0
    revise_turn = TurnInput(
        context_id="ctx-005",
        action_data={"name": REVISE_STAGE, "context": {"stage": "needs"}},
    )
    out = execute_turn(store, revise_turn)

    assert out.outcome is not None
    assert out.outcome.handled
    assert out.session.active_stage == 0
    assert "Problem and users" in out.reply_text
    # Surface re-emitted
    assert len(out.a2ui_messages) == 3


def test_full_business_intake_walkthrough_to_completion(store: InMemorySessionStore) -> None:
    ctx = "ctx-walkthrough"

    # Turn 1: Kickoff
    t1 = execute_turn(store, TurnInput(context_id=ctx, user_text="Claims triage"))
    assert t1.session.active_stage == 0
    assert not t1.is_complete

    # Turn 2: Commit Stage 0 (needs)
    t2 = execute_turn(
        store,
        TurnInput(
            context_id=ctx,
            action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD},
        ),
    )
    assert t2.session.active_stage == 1
    assert t2.outcome.advanced

    # Turn 3: Commit Stage 1 (sizing)
    t3 = execute_turn(
        store,
        TurnInput(
            context_id=ctx,
            action_data={"name": COMMIT_STAGE, "context": SIZING_PAYLOAD},
        ),
    )
    assert t3.session.active_stage == 2
    assert t3.outcome.advanced

    # Turn 4: Commit Stage 2 (data)
    t4 = execute_turn(
        store,
        TurnInput(
            context_id=ctx,
            action_data={"name": COMMIT_STAGE, "context": DATA_PAYLOAD},
        ),
    )
    assert t4.session.active_stage == 3
    assert t4.outcome.advanced

    # Turn 5: Commit Stage 3 (ownership)
    t5 = execute_turn(
        store,
        TurnInput(
            context_id=ctx,
            action_data={"name": COMMIT_STAGE, "context": OWNERSHIP_PAYLOAD},
        ),
    )
    assert t5.outcome.advanced
    assert t5.outcome.ready_to_finalize
    assert t5.is_complete
    assert "qualification complete" in t5.reply_text.lower()
    # All 4 stages are recorded and confirmed
    assert t5.session.record.business.user_count == 12
    assert t5.session.record.sizing.task_frequency_weekly == 5.0
    assert t5.session.record.technical.data_sources == ["sharepoint"]
    assert t5.session.record.meta.submitter == "Ana Ruiz"
    # Derived hours saved calculated automatically
    assert t5.session.record.derived.total_annual_team_hours_saved is not None
    assert t5.session.record.derived.total_annual_team_hours_saved > 0
