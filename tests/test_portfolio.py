"""Tests for Activity 3: Portfolio Prioritization & Analysis (`portfolio review`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors.sharepoint import SharePointConnector
from qualify.export.portfolio import PORTFOLIO_REPORT_FILENAME, render_portfolio_report
from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import (
    Business,
    Meta,
    Network,
    Security,
    Sizing,
    Technical,
    UseCaseRecord,
)
from qualify.scoring.portfolio import evaluate_opportunity, evaluate_portfolio
from qualify.sinks.session import InMemorySessionStore


def _make_record(
    record_id: str,
    name: str,
    *,
    dept: str = "Finance",
    users: int = 100,
    freq: float = 5.0,
    saved_mins: float = 30.0,
    level: CapabilityLevel = CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,
    transit_blocker: str | None = None,
) -> UseCaseRecord:
    return UseCaseRecord(
        meta=Meta(record_id=record_id, initiative_name=name, department_bu=dept),
        business=Business(
            user_count=users,
            problem_description="Manual contract triage across repositories.",
            expected_impacts="Faster turnaround and reduced SLA breaches across global teams.",
        ),
        sizing=Sizing(
            task_frequency_weekly=freq,
            baseline_minutes_per_task=45.0,
            target_minutes_saved_per_task=saved_mins,
        ),
        technical=Technical(
            data_sources=["sharepoint"],
            capability_level=level,
            network=Network(transit_blocker_status=transit_blocker),
            security=Security(data_classification="internal"),
        ),
    )


def test_quadrant_classification_all_four_quadrants() -> None:
    # 1. Quick Win: High Value (>=3) + High Feasibility (>=4)
    quick_win = _make_record(
        "UC-2026-QW0001",
        "Invoice Triage Assistant",
        users=120,
        freq=5.0,
        saved_mins=30.0,  # 15,000 hrs/yr -> value 5
        level=CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,  # feasibility 4
    )
    ev_qw = evaluate_opportunity(quick_win)
    assert ev_qw.business_value_score == 5
    assert ev_qw.feasibility_score == 4
    assert ev_qw.quadrant == "Quick Wins"
    assert quick_win.scoring.category == "Quick Wins"

    # 2. Strategic Bet: High Value (>=3) + Complex High-Code Tier (Feasibility 2-3)
    strategic_bet = _make_record(
        "UC-2026-SB0002",
        "Global SAP Supply Chain Orchestrator",
        users=200,
        freq=4.0,
        saved_mins=30.0,  # 20,000 hrs/yr -> value 5
        level=CapabilityLevel.HIGH_CODE_AGENT,  # feasibility 2
    )
    ev_sb = evaluate_opportunity(strategic_bet)
    assert ev_sb.business_value_score == 5
    assert ev_sb.feasibility_score == 2
    assert ev_sb.quadrant == "Strategic Bets"

    # 3. Departmental Niche: Lower Value (<3) + High Feasibility (>=4)
    niche = _make_record(
        "UC-2026-DN0003",
        "Team Meeting Notes Formatter",
        users=5,
        freq=1.0,
        saved_mins=10.0,  # ~41.7 hrs/yr -> value 2
        level=CapabilityLevel.CUSTOM_SKILL,  # feasibility 5
    )
    ev_niche = evaluate_opportunity(niche)
    assert ev_niche.business_value_score <= 2
    assert ev_niche.feasibility_score == 5
    assert ev_niche.quadrant == "Departmental Niche"

    # 4. Deprioritized / Blocked: Hard technical blocker (Airgapped network 2.4)
    blocked = _make_record(
        "UC-2026-BL0004",
        "Airgapped Plant Controller",
        users=300,
        freq=5.0,
        saved_mins=30.0,
        level=CapabilityLevel.HIGH_CODE_AGENT,
        transit_blocker="airgapped",
    )
    ev_blocked = evaluate_opportunity(blocked, has_dossier=True)
    assert ev_blocked.has_hard_blocker is True
    assert ev_blocked.feasibility_score == 1
    assert ev_blocked.quadrant == "Deprioritized"
    assert ev_blocked.priority_status == "Blocked"


def test_evaluate_portfolio_and_markdown_rendering() -> None:
    r1 = _make_record(
        "UC-2026-QW0001",
        "Invoice Triage Assistant",
        users=120,
        freq=5.0,
        saved_mins=30.0,
        level=CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,
    )
    r2 = _make_record(
        "UC-2026-SB0002",
        "SAP ERP Copilot",
        users=80,
        freq=4.0,
        saved_mins=30.0,
        level=CapabilityLevel.HIGH_CODE_AGENT,
    )
    summary = evaluate_portfolio(
        [
            (r2, {"hasBrief": True, "hasDossier": False, "webUrl": "https://sp.example/sb"}),
            (r1, {"hasBrief": True, "hasDossier": True, "webUrl": "https://sp.example/qw"}),
        ]
    )
    assert summary.total_count == 2
    assert summary.pending_tech_review_count == 1
    # Quick Win should be ordered ahead of Strategic Bet
    assert summary.evaluations[0].record_id == "UC-2026-QW0001"
    assert summary.evaluations[1].record_id == "UC-2026-SB0002"

    report_md = render_portfolio_report(
        summary, report_url="https://sp.example/Portfolio_Prioritization_Report.md"
    )
    assert "AI CoE Portfolio Prioritization Report" in report_md
    assert "Invoice Triage Assistant" in report_md
    assert "SAP ERP Copilot" in report_md
    assert "Quick Wins" in report_md
    assert "Strategic Bets" in report_md
    assert PORTFOLIO_REPORT_FILENAME in report_md


def test_portfolio_review_chat_turn_and_followup_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_sp = SharePointConnector(mock_dir=tmp_path / "sp_mock")
    monkeypatch.setattr(
        "qualify.connectors.sharepoint.get_sharepoint_connector", lambda: mock_sp
    )

    r1 = _make_record(
        "UC-2026-0CD0BC",
        "AAA",
        users=100,
        freq=4.0,
        saved_mins=30.0,
        level=CapabilityLevel.HIGH_CODE_AGENT,
    )
    mock_sp.sync_opportunity(r1, pack_name="business")

    store = InMemorySessionStore()
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-portfolio-1", user_text="portfolio review"),
    )

    assert "AI CoE Portfolio Prioritization Report" in out.reply_text
    assert "AAA" in out.reply_text
    assert "UC-2026-0CD0BC" in out.reply_text
    assert len(out.session.pending_review_choices) == 1

    # Verify Portfolio_Prioritization_Report.md was written to SharePoint
    report_file = (
        tmp_path
        / "sp_mock"
        / "drives"
        / "Documents"
        / "Qualification Opportunities"
        / PORTFOLIO_REPORT_FILENAME
    )
    assert report_file.is_file()

    # Follow-up turn: user selects "let's start with 1" (or "let's start with AAA") from the portfolio report
    out2 = execute_turn(
        store,
        TurnInput(context_id="ctx-portfolio-1", user_text="let's start with 1"),
    )
    assert out2.session.pack_name == "tech"
    assert out2.session.record.meta.record_id == "UC-2026-0CD0BC"
    assert "Technical Architecture Review" in out2.reply_text


def test_phase1_record_with_overlapping_fields_retains_indicative_feasibility() -> None:
    """Guards against Phase 1 records with data_sources + classification + owner getting 18% readiness (1/5 feasibility)."""
    from qualify.schema.use_case_record import Grounding, Proposed

    rec = _make_record(
        "UC-2026-308D3E",
        "a",
        dept="b",
        users=12,
        freq=10.0,
        saved_mins=10.0,  # 1,000 hrs/yr -> Value 3/5
        level=CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,
    )
    rec.technical.data_sources = ["google_drive", "confluence"]
    rec.technical.grounding = Grounding(acl_preservation_required=True)
    rec.proposed = Proposed(business_owner="Alice", domain_sme="Bob")

    ev = evaluate_opportunity(rec, has_brief=True, has_dossier=False)
    assert ev.is_indicative_feasibility is True
    assert ev.feasibility_score == 4
    assert ev.business_value_score == 3
    assert ev.quadrant == "Quick Wins"


def test_welcome_banner_and_help_command() -> None:
    store = InMemorySessionStore()
    # 1. Explicit help command
    out_help = execute_turn(
        store,
        TurnInput(context_id="ctx-welcome-1", user_text="what can you do?"),
    )
    assert "Welcome to the Gemini Enterprise AI Qualification & CoE Agent" in out_help.reply_text
    assert "technical review" in out_help.reply_text
    assert "portfolio review" in out_help.reply_text

    # 2. First turn opening Stage 1 / Sign-In card includes the welcome banner
    out_first = execute_turn(
        store,
        TurnInput(context_id="ctx-welcome-2", user_text="hi"),
    )
    assert "Welcome to the Gemini Enterprise AI Qualification & CoE Agent" in out_first.reply_text


def test_capability_grounding_anti_overcommitment_delegation() -> None:
    from qualify.agent.turn import load_instructions
    from qualify.scoring.business_tier import classify_capability

    # 1. Skill is loaded into agent instructions
    inst = load_instructions()
    assert "ge-capability-grounding" in inst
    assert "Zero Overcommitment" in inst

    # 2. Unknown data sources -> delegates to Pro-Code (Level 5 Custom MCP), never No-Code/Low-Code
    rec_unknown = UseCaseRecord(
        meta=Meta(record_id="UC-2026-UNKN01", initiative_name="Mystery Data Helper"),
        business=Business(problem_description="Help analysts look up policy rules."),
        technical=Technical(data_sources=["unknown"]),
    )
    classify_capability(rec_unknown)
    assert rec_unknown.technical.capability_level == CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP
    assert "Not sure yet (`unknown`)" in (rec_unknown.technical.capability_rationale or "")

    # 3a. Writes to SharePoint: its GE connector documents end-user actions, so
    #     this stays Low-Code (Level 4) and the rationale cites the doc page.
    rec_sp_write = UseCaseRecord(
        meta=Meta(record_id="UC-2026-SPWR02", initiative_name="SharePoint Mutator"),
        business=Business(
            problem_description="Read contract drafts and write updated clauses back to SharePoint.",
            user_stories="Agent must write and update record metadata in SharePoint libraries.",
        ),
        technical=Technical(data_sources=["sharepoint"]),
    )
    classify_capability(rec_sp_write)
    assert rec_sp_write.technical.capability_level == CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT
    sp_rationale = rec_sp_write.technical.capability_rationale or ""
    assert "end-user actions documented" in sp_rationale
    assert "cloud.google.com/gemini/enterprise/docs/connectors/" in sp_rationale

    # 3b. Writes to an ingest-only native connector (BigQuery) -> Level 5
    rec_bq_write = UseCaseRecord(
        meta=Meta(record_id="UC-2026-BQWR02", initiative_name="BigQuery Mutator"),
        business=Business(
            problem_description="Read sales tables and write corrected forecasts back.",
            user_stories="Agent must update rows in BigQuery.",
        ),
        technical=Technical(data_sources=["bigquery"]),
    )
    classify_capability(rec_bq_write)
    assert rec_bq_write.technical.capability_level == CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP
    assert "read/retrieval-scoped" in (rec_bq_write.technical.capability_rationale or "")

    # 4. Cross-system mutations across >=2 systems -> delegates to Pro-Code (Level 6 ADK)
    rec_multi_write = UseCaseRecord(
        meta=Meta(record_id="UC-2026-MLTW03", initiative_name="Cross-CRM Sync"),
        business=Business(
            problem_description="Synchronize customer escalations.",
            user_stories="Update record in Salesforce and create ticket in Jira automatically.",
        ),
        technical=Technical(data_sources=["salesforce", "jira"]),
    )
    classify_capability(rec_multi_write)
    assert rec_multi_write.technical.capability_level == CapabilityLevel.HIGH_CODE_AGENT


