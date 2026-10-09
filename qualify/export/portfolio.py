"""Activity 3 deliverable renderer: `Portfolio_Prioritization_Report.md`.

Renders the CoE Portfolio Prioritization & Analysis report from a
`PortfolioSummary` for both chat display and persistence in SharePoint.
"""

from __future__ import annotations

from datetime import date

from qualify.scoring.portfolio import (
    QUADRANT_ORDER,
    OpportunityEvaluation,
    PortfolioSummary,
    QuadrantName,
)
from qualify.scoring.technical import KEY2_THRESHOLD

PORTFOLIO_REPORT_FILENAME = "Portfolio_Prioritization_Report.md"

_QUADRANT_ICONS: dict[QuadrantName, str] = {
    "Quick Wins": "🟢 Quick Wins (Immediate Citizen / Low-Code ROI)",
    "Strategic Bets": "🔵 Strategic Bets (High-Value CoE / Pro-Code Builds)",
    "Departmental Niche": "🟡 Departmental Niche (Localized Self-Service)",
    "Deprioritized": "⚪ Deprioritized / Blocked",
}


def _fmt_hours(hours: float | None) -> str:
    if hours is None:
        return "Unsized"
    return f"{hours:,.0f} hrs/yr"


def _fmt_feasibility(ev: OpportunityEvaluation) -> str:
    suffix = "*" if ev.is_indicative_feasibility else ""
    return f"{ev.feasibility_score}/5{suffix}"


def _fmt_initiative_link(ev: OpportunityEvaluation) -> str:
    label = f"**{ev.initiative_name}** (`{ev.record_id}`)"
    if ev.folder_url:
        return f"[{ev.initiative_name}]({ev.folder_url}) (`{ev.record_id}`)"
    return label


def render_portfolio_report(
    summary: PortfolioSummary,
    *,
    report_url: str | None = None,
    generated_on: date | None = None,
) -> str:
    """Renders the Activity 3 Portfolio Prioritization markdown report."""
    today = (generated_on or date.today()).isoformat()
    by_q = summary.by_quadrant()

    lines: list[str] = [
        "# 📊 AI CoE Portfolio Prioritization Report",
        "",
        f"**Generated:** `{today}` | **Qualified Opportunities:** `{summary.total_count}` | "
        f"**Total Annual Savings:** `{summary.total_annual_hours_saved:,.0f} hrs/yr` | "
        f"**Total Users Reached:** `{summary.total_users_reached:,}`",
    ]
    if report_url:
        lines.append(
            f"**SharePoint Report:** [Open Portfolio Prioritization Report in SharePoint]({report_url})"
        )

    lines.extend(
        [
            "",
            "## 1. Portfolio Executive Summary",
            "",
            "| Metric | Count / Value | Notes |",
            "| :--- | :--- | :--- |",
            f"| **🟢 Quick Wins** | **{len(by_q['Quick Wins'])}** | High Business Value (>=3/5) & High Feasibility (>=4/5) |",
            f"| **🔵 Strategic Bets** | **{len(by_q['Strategic Bets'])}** | High Business Value (>=3/5) & Custom MCP / High-Code Architecture |",
            f"| **🟡 Departmental Niche** | **{len(by_q['Departmental Niche'])}** | Localized Value (<3/5) & High Self-Service Feasibility (>=4/5) |",
            f"| **⚪ Deprioritized / Blocked** | **{len(by_q['Deprioritized'])}** | Hard technical blockers or low value & low feasibility |",
            f"| **Pending Gate 2 Tech Review** | **{summary.pending_tech_review_count}** | Business Value Brief completed; awaiting Technical Architecture Review |",
            f"| **Gate 2 Cleared (>={KEY2_THRESHOLD}% Readiness)** | **{summary.key2_ready_count}** | Passed 22-subcriteria technical review with both hard-blocker checks (2.4, 3.5) cleared |",
            "",
            "## 2. Ranked Portfolio Matrix",
            "",
            "| Rank | Initiative (`Record ID`) | Dept / BU | Value | Feasibility | Est. Annual Savings | GE Capability Tier | Status | Quadrant |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for idx, ev in enumerate(summary.evaluations, start=1):
        lines.append(
            f"| {idx} | {_fmt_initiative_link(ev)} | {ev.department_bu} | "
            f"**{ev.business_value_score}/5** | **{_fmt_feasibility(ev)}** | "
            f"{_fmt_hours(ev.annual_hours_saved)} | {ev.capability_level.label} | "
            f"{ev.priority_status} | **{ev.quadrant}** |"
        )

    lines.extend(
        [
            "",
            "_\\* Feasibility marked with `*` is indicative (derived from Phase 1 capability tier) and awaits Phase 2 Technical Architecture Review._",
            "",
            "## 3. Actionable Quadrant Breakdown & CoE Recommendations",
            "",
        ]
    )

    for q_name in QUADRANT_ORDER:
        items = by_q[q_name]
        lines.append(f"### {_QUADRANT_ICONS[q_name]} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("_No opportunities currently in this quadrant._")
            lines.append("")
            continue

        for ev in items:
            lines.append(
                f"- {_fmt_initiative_link(ev)} — **Value `{ev.business_value_score}/5`** ({ev.value_rationale}) · "
                f"**Feasibility `{_fmt_feasibility(ev)}`** ({ev.feasibility_rationale})"
            )
            lines.append(f"  - **Recommended Action:** {ev.recommended_next_step}")
        lines.append("")

    if summary.pending_tech_review_count > 0:
        lines.extend(
            [
                "---",
                "💡 **Tip:** Type `technical review <Initiative Name or Record ID>` to run the Phase 2 Technical Architecture Review on any pending opportunity.",
            ]
        )

    return "\n".join(lines)
