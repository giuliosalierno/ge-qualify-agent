"""The portfolio side panel: quadrant matrix, ranked table, actions.

Replaces the long markdown report in chat. The markdown is still produced
and saved to SharePoint by the caller, so nothing is lost outside GE.
"""

from __future__ import annotations

from typing import Any

from qualify.a2ui.views import components as ui
from qualify.a2ui.views.charts import matrix_points, quadrant_matrix_spec
from qualify.a2ui.views.events import OPEN_BRIEF, START_TECH_REVIEW
from qualify.schema.capability import CapabilityLevel
from qualify.scoring.portfolio import (
    QUADRANT_ORDER,
    OpportunityEvaluation,
    PortfolioSummary,
)

_DATA_ROOT = "/ui/portfolio"

QUADRANT_ICONS = {
    "Quick Wins": "🟢",
    "Strategic Bets": "🔵",
    "Departmental Niche": "🟡",
    "Deprioritized": "⚪",
}

#: Levels whose delivery needs a Phase 2 review before build.
_REVIEW_LEVELS = (
    CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP,
    CapabilityLevel.HIGH_CODE_AGENT,
)


def _hours(value: float | None) -> str:
    return f"{value:,.0f}" if value else "Unsized"


def headline(summary: PortfolioSummary) -> str:
    """One line of totals, shared by the panel and the chat reply."""
    quick = len(summary.by_quadrant()["Quick Wins"])
    return (
        f"{summary.total_count} opportunities · "
        f"{summary.total_annual_hours_saved:,.0f} hrs/yr · "
        f"{summary.total_users_reached:,} users · "
        f"{quick} Quick Wins · "
        f"{summary.pending_tech_review_count} pending tech review"
    )


def needs_tech_review(ev: OpportunityEvaluation) -> bool:
    return not ev.has_dossier and not ev.has_hard_blocker


#: How the Ranked list columns are computed. Mirrors
#: ``scoring.portfolio._score_business_value``, ``_score_feasibility`` and
#: ``_assign_quadrant``; change both together.
#:
#: Rendered inside a collapsible MaterialExpansionPanel using ``body`` Text
#: (GE renders markdown in ``body`` but shows it raw in ``caption``).
_SCORING_NOTES: tuple[tuple[str, str], ...] = (
    (
        "pf-how-value",
        "**📈 Value (1–5) — Annual Time Saved**\n\n"
        "- **5 / 5:** 7,500+ hrs/yr saved across the team\n"
        "- **4 / 5:** 1,750 – 7,499 hrs/yr\n"
        "- **3 / 5:** 350 – 1,749 hrs/yr\n"
        "- **2 / 5:** 40 – 349 hrs/yr\n"
        "- **1 / 5:** Under 40 hrs/yr\n"
        "- _Modifiers:_ Up to **+0.6** for 250+ users and quantified business impacts. "
        "If hours aren't sized yet, user count is used as a proxy.",
    ),
    (
        "pf-how-feas",
        "**🛠️ Feasibility (1–5) — Delivery Readiness**\n\n"
        "- **Before Phase 2 (indicative, shown as `4/5*`):** Estimated from the recommended "
        "build tier and official Gemini Enterprise connector coverage:\n"
        "  - **5 / 5:** No-code Assistant (built-in connectors)\n"
        "  - **4 / 5:** Low-code Workflow Builder agent\n"
        "  - **3 / 5:** Agent + 1 custom MCP connector\n"
        "  - **2 / 5:** Custom multi-system ADK agent\n"
        "- **After Phase 2 (verified):** Replaced by the Technical Review readiness score "
        "(**85%+ → 5** · **70%+ → 4** · **50%+ → 3** · **30%+ → 2** · **<30% → 1**).\n"
        "- **Hard blocker:** Any data-sovereignty or cloud-processing blocker caps Feasibility at **1 / 5**.",
    ),
    (
        "pf-how-quadrant",
        "**🧭 Quadrant Assignment & Ranking Order**\n\n"
        "- **🟢 Quick Wins:** Value 3–5 and Feasibility 4–5\n"
        "- **🔵 Strategic Bets:** Value 3–5 and Feasibility 2–3\n"
        "- **🟡 Departmental Niche:** Value 1–2 and Feasibility 4–5\n"
        "- **⚪ Deprioritized:** Lower scores or any hard blocker\n"
        "- **Ranking order:** Grouped by quadrant (Quick Wins first), then ranked by composite score "
        "(**60% value + 40% feasibility**), then annual hours saved.",
    ),
)
_SCORING_NOTE_IDS = [cid for cid, _ in _SCORING_NOTES]


def _scoring_note() -> list[ui.Component]:
    children: list[str] = []
    nodes: list[ui.Component] = []
    for idx, (cid, value) in enumerate(_SCORING_NOTES):
        if idx > 0:
            rule_id = f"{cid}-rule"
            children.append(rule_id)
            nodes.append(ui.divider(rule_id))
        children.append(cid)
        nodes.append(ui.text(cid, value, "body"))
    panel = ui.panel(
        "pf-how-panel",
        "ℹ️ Scoring & Quadrant Guide",
        children,
        description="How Value (1–5), Feasibility (1–5), and Quadrant rankings are calculated",
        expanded=False,
    )
    return [panel, *nodes]


def _ranked_rows(summary: PortfolioSummary) -> list[dict[str, Any]]:
    rows = []
    for rank, ev in enumerate(summary.evaluations, start=1):
        feas = f"{ev.feasibility_score}/5" + ("*" if ev.is_indicative_feasibility else "")
        rows.append(
            {
                "rank": rank,
                "name": ev.initiative_name,
                "recordId": ev.record_id,
                "dept": ev.department_bu,
                "value": f"{ev.business_value_score}/5",
                "feasibility": feas,
                "hours": _hours(ev.annual_hours_saved),
                "quadrant": f"{QUADRANT_ICONS[ev.quadrant]} {ev.quadrant}",
                "status": ev.priority_status,
            }
        )
    return rows


def _opportunity_block(ev: OpportunityEvaluation, idx: int) -> tuple[list[str], list[ui.Component]]:
    """Description plus buttons for one opportunity in the Actions tab."""
    base = f"pf-op-{idx}"
    ids = [f"{base}-name", f"{base}-detail", f"{base}-next"]
    # The name doubles as the text-link fallback for the open button below.
    name = f"[{ev.initiative_name}]({ev.folder_url})" if ev.folder_url else ev.initiative_name
    nodes: list[ui.Component] = [
        ui.text(ids[0], f"**{name}** · `{ev.record_id}`", "body"),
        ui.text(
            ids[1],
            f"Value {ev.business_value_score}/5 · Feasibility {ev.feasibility_score}/5"
            + ("*" if ev.is_indicative_feasibility else "")
            + f" · {_hours(ev.annual_hours_saved)} hrs/yr · {ev.priority_status}",
            "caption",
        ),
        ui.text(ids[2], f"➡️ {ev.recommended_next_step}", "caption"),
    ]

    brief_id = f"{base}-brief"
    ids.append(brief_id)
    nodes += ui.event_button(brief_id, "Open brief", OPEN_BRIEF, {"recordId": ev.record_id})

    if ev.folder_url:
        open_id = f"{base}-open"
        ids.append(open_id)
        nodes.append(
            ui.open_url_button(
                open_id, f"Open in {ui.storage_name(ev.folder_url)}", ev.folder_url, icon="folder_open"
            )[0]
        )

    if needs_tech_review(ev):
        review_id = f"{base}-review"
        ids.append(review_id)
        nodes += ui.event_button(
            review_id,
            "Start technical review",
            START_TECH_REVIEW,
            {"recordId": ev.record_id},
            primary=ev.capability_level in _REVIEW_LEVELS or ev.quadrant == "Strategic Bets",
        )

    rule = f"{base}-rule"
    ids.append(rule)
    nodes.append(ui.divider(rule))
    return ids, nodes


def build_portfolio_view(
    summary: PortfolioSummary,
    surface_id: str,
    *,
    source_note: str | None = None,
    signin: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The full message sequence for the portfolio side panel.

    ``signin`` is ``(url, account_label)`` when the user is not signed in to
    document storage; the header then carries a sign-in button.
    """
    nodes: list[ui.Component] = []

    # --- Matrix tab -----------------------------------------------------------
    nodes += [
        ui.column("pf-matrix", ["pf-chart", "pf-chart-note"]),
        ui.chart("pf-chart", f"{_DATA_ROOT}/chart", height=440),
        ui.text(
            "pf-chart-note",
            "Bubble size = hours saved per year · ⛔ = hard blocker · "
            "Quick Win = value ≥ 3 and feasibility ≥ 4 · Hover a bubble for details. "
            "Feasibility marked * in the ranked list is indicative until the "
            "technical review.",
            "caption",
        ),
    ]

    # --- Ranked tab -----------------------------------------------------------
    nodes += [
        ui.column("pf-ranked", ["pf-table", "pf-how-panel"]),
        *_scoring_note(),
    ]
    nodes.append(
        ui.table(
            "pf-table",
            [
                ("#", "rank"),
                ("Initiative", "name"),
                ("Record", "recordId"),
                ("Dept / BU", "dept"),
                ("Value", "value"),
                ("Feasibility", "feasibility"),
                ("Hours / yr", "hours"),
                ("Quadrant", "quadrant"),
                ("Status", "status"),
            ],
            _ranked_rows(summary),
            caption="Ranked by quadrant, then composite score (60% value, 40% feasibility)",
        )
    )

    # --- Actions tab ----------------------------------------------------------
    panel_ids: list[str] = []
    idx = 0
    for quadrant in QUADRANT_ORDER:
        group = summary.by_quadrant()[quadrant]
        if not group:
            continue
        pid = f"pf-q-{QUADRANT_ORDER.index(quadrant)}"
        child_ids: list[str] = []
        for ev in group:
            ids, block = _opportunity_block(ev, idx)
            child_ids += ids
            nodes += block
            idx += 1
        nodes.append(
            ui.panel(
                pid,
                f"{QUADRANT_ICONS[quadrant]} {quadrant} ({len(group)})",
                child_ids,
                expanded=quadrant in ("Quick Wins", "Strategic Bets"),
            )
        )
        panel_ids.append(pid)
    if not panel_ids:
        nodes.append(ui.text("pf-empty", "No finished qualifications yet.", "body"))
        panel_ids.append("pf-empty")
    nodes.append(ui.column("pf-actions", panel_ids))

    # --- Header + root --------------------------------------------------------
    header_ids = ["pf-title", "pf-kpis"]
    nodes += [
        ui.text("pf-title", "AI CoE Portfolio Prioritization", "h3"),
        ui.text("pf-kpis", headline(summary), "caption"),
    ]
    if source_note:
        header_ids.append("pf-source")
        nodes.append(ui.text("pf-source", source_note, "body" if signin else "caption"))
    if signin:
        url, account = signin
        header_ids += ["pf-signin", "pf-signin-link"]
        nodes += ui.open_url_button(
            "pf-signin",
            f"Sign in with {account}",
            url,
            icon="login",
            primary=True,
            fallback=f"Sign in to open the files: [sign in with {account}]({url}), "
            "then type `portfolio review` again.",
        )
    # Matrix is deliberately NOT the first tab. GE's VegaChart takes its width
    # from the panel when it first draws, ignoring the spec's fixed width, and
    # the first tab draws while the panel is still sliding open — so the chart
    # came out at about half width until a tab switch redrew it. A chart on a
    # later tab draws on click, into the fully open panel (the brief's chart,
    # on "Scores", never had the problem).
    nodes.append(
        ui.tabs(
            "pf-tabs",
            [("Ranked list", "pf-ranked"), ("Matrix", "pf-matrix"), ("Actions", "pf-actions")],
        )
    )
    root = ui.canvas_root(
        [*header_ids, "pf-tabs"],
        title="Portfolio prioritization",
        description=headline(summary),
        icon="insights",
    )

    data = {
        "ui": {
            "portfolio": {
                "chart": quadrant_matrix_spec(matrix_points(summary.evaluations)),
            }
        }
    }
    return ui.surface(surface_id, [root, *nodes], data)
