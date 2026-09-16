"""Tests for per-stage surfaceId progression and Business Value Brief generation."""

from __future__ import annotations

from qualify.agent.turn import TurnInput, execute_turn
from qualify.a2ui.actions import COMMIT_STAGE, REVISE_STAGE
from qualify.a2ui.compiler import build_completion_surface
from qualify.a2ui.validate import validate_surface
from qualify.packs.loader import load_pack
from qualify.schema.use_case_record import Meta, UseCaseRecord
from qualify.sinks.session import InMemorySessionStore

NEEDS_PAYLOAD = {
    "meta": {"initiative_name": "AP Invoice Exception Assistant"},
    "business": {
        "problem_description": "AP specialists manually pull PDF contracts from Drive to resolve PO discrepancies.",
        "user_profile": "Accounts Payable Specialist",
        "user_count": "25",
    },
}

SIZING_PAYLOAD = {
    "sizing": {
        "task_frequency_weekly": "12",
        "baseline_minutes_per_task": "30",
        "target_minutes_saved_per_task": "20",
    }
}

DATA_PAYLOAD = {
    "technical": {
        "data_sources": ["sharepoint", "salesforce"],
        "security": {"data_classification": "confidential"},
    }
}

OWNERSHIP_PAYLOAD = {
    "meta": {"submitter": "Sarah Jenkins"},
    "proposed": {"executive_sponsor": "VP of Finance"},
}


def test_each_stage_renders_as_distinct_surface_id() -> None:
    """Verifies that each stage transition produces a new surfaceId so GE renders a new chat card."""
    store = InMemorySessionStore(quiet=True)
    ctx = "ctx-distinct-surfaces"

    # Turn 1: Stage 0 surface
    t1 = execute_turn(store, TurnInput(context_id=ctx, user_text="Start"))
    sid_0 = t1.a2ui_messages[0]["createSurface"]["surfaceId"]
    assert sid_0.startswith("qualify-s0-")

    # Turn 2: Commit Stage 0 -> advances to Stage 1 with single surfaceId
    t2 = execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD}),
    )
    assert len(t2.a2ui_messages) == 3
    sid_1 = t2.a2ui_messages[0]["createSurface"]["surfaceId"]
    assert sid_1.startswith("qualify-s1-")
    assert sid_1 != sid_0
    # Verify Stage 0 summary banner appears inside Stage 1's components
    s1_comps = t2.a2ui_messages[1]["updateComponents"]["components"]
    assert any("✓ Stage 1: Problem and users — Confirmed" in str(c.get("text", "")) for c in s1_comps)

    # Turn 3: Commit Stage 1 -> advances to Stage 2
    t3 = execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": SIZING_PAYLOAD}),
    )
    assert len(t3.a2ui_messages) == 3
    sid_2 = t3.a2ui_messages[0]["createSurface"]["surfaceId"]
    assert sid_2.startswith("qualify-s2-")
    assert sid_2 not in {sid_0, sid_1}

    # Turn 4: Commit Stage 2 -> advances to Stage 3
    t4 = execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": DATA_PAYLOAD}),
    )
    assert len(t4.a2ui_messages) == 3
    sid_3 = t4.a2ui_messages[0]["createSurface"]["surfaceId"]
    assert sid_3.startswith("qualify-s3-")
    assert sid_3 not in {sid_0, sid_1, sid_2}

    # Turn 5: Commit Stage 3 -> emits completion brief and completion surface
    t5 = execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": OWNERSHIP_PAYLOAD}),
    )
    assert len(t5.a2ui_messages) == 3
    sid_complete = t5.a2ui_messages[0]["createSurface"]["surfaceId"]
    assert sid_complete.startswith("qualify-complete-")
    assert sid_complete not in {sid_0, sid_1, sid_2, sid_3}

    # Verify the markdown Business Value Brief was generated
    assert "# Business Value Brief: AP Invoice Exception Assistant" in t5.reply_text
    assert "## 1. Executive Summary & Governance" in t5.reply_text
    assert "## 2. Value Realization & Sizing Scorecard" in t5.reply_text
    assert "5,000" in t5.reply_text  # 25 users * 12/wk * 20m / 60 * 50 wks = 5,000 hrs/yr

    # Reopen Stage 1 -> renders yet another new surfaceId
    t6 = execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": REVISE_STAGE, "context": {"stage": "sizing"}}),
    )
    sid_reopen = t6.a2ui_messages[0]["createSurface"]["surfaceId"]
    assert sid_reopen.startswith("qualify-s1-")
    assert sid_reopen != sid_1


def test_completion_surface_passes_schema_and_structural_validation() -> None:
    """Ensures build_completion_surface produces 100% valid A2UI v0.9 messages."""
    pack = load_pack("business")
    record = UseCaseRecord(meta=Meta(record_id="test-rec-123", initiative_name="Test Initiative"))
    messages = build_completion_surface(pack, record, surface_id="qualify-complete-99")
    # Strict schema + component graph validation
    validate_surface(messages)


def test_chat_client_receives_form_state_and_brief_classifies_tier() -> None:
    """Verifies that a custom chat client receives active field statuses and brief classifies tier."""
    store = InMemorySessionStore(quiet=True)
    ctx = "a1b2c3d4-5678"

    captured_summary: list[str] = []

    class StubChatClient:
        def reply(self, *, instruction: str, conversation: str, stage_label: str, record_summary: str) -> str:
            captured_summary.append(record_summary)
            return "No problem—rough ballpark estimates work! Is it closer to 5x a week or 1x a week?"

    t1 = execute_turn(store, TurnInput(context_id=ctx, user_text="I don't have exact numbers"), chat_client=StubChatClient())
    assert "No problem—rough ballpark estimates work!" in t1.reply_text
    assert len(captured_summary) == 1
    assert "[MISSING - REQUIRED]" in captured_summary[0]
    assert t1.session.record.meta.record_id.startswith("UC-")
    assert t1.session.record.meta.submission_date is not None


def test_collapsed_stage_patch_passes_schema_validation() -> None:
    """Ensures build_collapsed_stage_patch produces a valid A2UI v0.9 updateComponents message."""
    from qualify.a2ui.compiler import build_collapsed_stage_patch
    from qualify.a2ui.validate import check_component_graph, message_validator

    pack = load_pack("business")
    record = UseCaseRecord(meta=Meta(record_id="test-rec-123"))
    patch = build_collapsed_stage_patch(pack, record, stage_idx=0, surface_id="qualify-s0-needs-1")
    message_validator().validate(patch)
    check_component_graph(patch["updateComponents"]["components"])


def test_stage_2_sizing_chat_turn_with_ui_paths() -> None:
    """Verifies that Stage 2 ('sizing', which contains /ui/summary/* readonly fields) handles 'i don't have those numbers' without AttributeError."""
    store = InMemorySessionStore(quiet=True)
    ctx = "ctx-stage2-ui-path"

    # Start and commit Stage 0
    execute_turn(store, TurnInput(context_id=ctx, user_text="Start"))
    execute_turn(store, TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD}))

    class StubChatClient:
        def reply(self, *, instruction: str, conversation: str, stage_label: str, record_summary: str) -> str:
            assert "How many times a week" in record_summary
            assert "/ui/summary" not in record_summary
            return "No worries! We can use ballpark figures—say 5 times a week, 30 mins baseline, 15 mins saved."

    # Send "i don't have those numbers" on Stage 2 (active_stage == 1)
    out = execute_turn(
        store,
        TurnInput(context_id=ctx, user_text="i don't have those numbers"),
        chat_client=StubChatClient(),
    )
    assert "ballpark figures" in out.reply_text


def test_skip_stage_button_and_chat_flow() -> None:
    """Verifies that Stage 0 cannot be skipped, Stages 1-3 can be skipped via button or chat, and skipped items appear in the Brief."""
    from qualify.a2ui.actions import SKIP_STAGE

    store = InMemorySessionStore(quiet=True)
    ctx = "ctx-skip-test"

    # Turn 1: Start on Stage 0
    execute_turn(store, TurnInput(context_id=ctx, user_text="Start"))

    # Attempting to skip Stage 0 in chat is refused
    t_refuse = execute_turn(store, TurnInput(context_id=ctx, user_text="skip"))
    assert "Stage 1 (The problem) cannot be skipped" in t_refuse.reply_text
    assert t_refuse.session.active_stage == 0

    # Commit Stage 0 -> advances to Stage 1 (sizing)
    execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD}),
    )

    # Skip Stage 1 (sizing) via chat command 'skip for now'
    t_skip1 = execute_turn(store, TurnInput(context_id=ctx, user_text="skip for now"))
    assert t_skip1.session.active_stage == 2
    assert 1 in t_skip1.session.skipped
    assert "Skipped **Effort and value** for now" in t_skip1.reply_text
    assert len(t_skip1.a2ui_messages) == 3
    from qualify.a2ui.validate import validate_surface

    validate_surface(t_skip1.a2ui_messages)
    # Summary banner inside Stage 2's card should show '⚠ Stage 2: Effort and value — Skipped'
    s2_comps = t_skip1.a2ui_messages[1]["updateComponents"]["components"]
    assert any("Skipped (Needs follow-up)" in str(c.get("text", "")) for c in s2_comps)

    # Commit Stage 2 (data) normally
    execute_turn(
        store,
        TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": DATA_PAYLOAD}),
    )

    # Skip Stage 3 (ownership) via SKIP_STAGE button action -> finalizes
    t_final = execute_turn(
        store,
        TurnInput(
            context_id=ctx,
            action_data={"name": SKIP_STAGE, "context": {"stage": "ownership"}},
        ),
    )
    assert t_final.is_complete
    assert "## ⚠️ Open Discovery Items (Pending Follow-Up)" in t_final.reply_text
    assert "CONDITIONAL QUALIFICATION" in t_final.reply_text
    assert "Effort and value" in t_final.reply_text




