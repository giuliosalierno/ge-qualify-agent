"""The Business Value Brief side panel shown when Phase 1 finishes.

Three tabs:

* **Brief**: the deliverable in collapsible sections.
* **Scores**: value and feasibility with their rationale, and the use case
  plotted on the same quadrant matrix as the portfolio.
* **Next steps**: the recommended action and buttons for it, plus reopen
  buttons for each stage when the brief belongs to this conversation.

Built from the same ``UseCaseRecord`` as ``export.brief.render_business_brief``
and scored with ``scoring.portfolio.evaluate_opportunity``, so the panel, the
markdown brief and the portfolio always agree.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from qualify.a2ui.actions import REVISE_STAGE
from qualify.a2ui.compiler import summary_strings
from qualify.a2ui.views import components as ui
from qualify.a2ui.views.charts import matrix_points, quadrant_matrix_spec
from qualify.a2ui.views.events import OPEN_PORTFOLIO, OPEN_WORKSPACE, START_TECH_REVIEW
from qualify.a2ui.views.portfolio import QUADRANT_ICONS, needs_tech_review
from qualify.packs.loader import Pack
from qualify.schema.use_case_record import UseCaseRecord
from qualify.scoring.business_tier import business_summary
from qualify.scoring.portfolio import OpportunityEvaluation, evaluate_opportunity

_DATA_ROOT = "/ui/brief"


def _or(value: Any, fallback: str = "Not provided") -> str:
    if value is None or value == "" or value == []:
        return fallback
    return str(value)


def _open_items(record: UseCaseRecord, pack: Pack, skipped: set[int]) -> list[str]:
    from qualify.a2ui.provenance import missing_required  # noqa: PLC0415

    items = []
    for idx, stage in enumerate(pack.stages):
        missing = missing_required(record, stage)
        if missing or idx in skipped:
            labels = ", ".join(f.label for f in missing) or "Pending confirmation"
            items.append(f"**{stage.label}**: {labels}")
    return items


def _brief_sections(record: UseCaseRecord, open_items: list[str]) -> tuple[list[str], list[ui.Component]]:
    meta, biz, sizing, tech, prop = (
        record.meta,
        record.business,
        record.sizing,
        record.technical,
        record.proposed,
    )
    sums = summary_strings(record)

    sources = [s for s in tech.data_sources if s != "other"]
    if tech.other_data_sources:
        sources.append(tech.other_data_sources)

    sections: list[tuple[str, str, str, bool]] = []  # (id, title, markdown, expanded)
    if open_items:
        sections.append(
            ("br-open", "⚠️ Open discovery items", "\n".join(f"- {i}" for i in open_items), True)
        )
    sections += [
        (
            "br-exec",
            "Executive summary",
            "\n".join(
                [
                    f"- **Department / BU:** {_or(meta.department_bu, 'Unspecified')}",
                    f"- **Executive sponsor:** {_or(prop.executive_sponsor, 'Pending confirmation')}",
                    f"- **Business owner:** {_or(prop.business_owner or meta.submitter, 'Unspecified')}",
                    f"- **Target users:** {_or(biz.user_profile)}",
                    f"- **Headcount:** {biz.user_count or 0:,}",
                ]
            ),
            not open_items,
        ),
        (
            "br-problem",
            "Problem and user stories",
            _or(biz.problem_description, "_No problem description recorded._")
            + (f"\n\n**User stories**\n\n{biz.user_stories}" if biz.user_stories else ""),
            False,
        ),
        (
            "br-value",
            "Value sizing",
            "\n".join(
                [
                    f"**{sums['hours_line']}**",
                    "",
                    f"- Runs per user per week: {_or(sizing.task_frequency_weekly)}",
                    f"- Minutes per run today: {_or(sizing.baseline_minutes_per_task)}",
                    f"- Minutes saved per run: {_or(sizing.target_minutes_saved_per_task)}",
                    f"- Basis: {sums['hours_basis'] or 'n/a'}",
                ]
            )
            + (f"\n\n**Beyond hours**\n\n{biz.expected_impacts}" if biz.expected_impacts else ""),
            False,
        ),
        (
            "br-systems",
            "Systems and data",
            "\n".join(
                [
                    f"- **Systems touched:** {', '.join(sources) if sources else 'None specified'}",
                    f"- **Highest data classification:** "
                    f"{_or(tech.security.data_classification, 'Unspecified').capitalize()}",
                ]
            ),
            False,
        ),
        (
            "br-capability",
            "Recommended approach",
            business_summary(record),
            False,
        ),
    ]

    ids: list[str] = []
    nodes: list[ui.Component] = []
    for sid, title, body, expanded in sections:
        nodes.append(ui.panel(sid, title, [f"{sid}-body"], expanded=expanded))
        nodes.append(ui.text(f"{sid}-body", body, "body"))
        ids.append(sid)
    return ids, nodes


def _scores_tab(ev: OpportunityEvaluation) -> list[ui.Component]:
    feas_note = " (indicative until the technical review)" if ev.is_indicative_feasibility else ""
    return [
        ui.column("br-scores", ["br-quadrant", "br-value-score", "br-feas-score", "br-chart"]),
        ui.text(
            "br-quadrant",
            f"{QUADRANT_ICONS[ev.quadrant]} **{ev.quadrant}** · {ev.priority_status}",
            "h4",
        ),
        ui.text(
            "br-value-score",
            f"**Business value {ev.business_value_score}/5.** {ev.value_rationale}",
            "body",
        ),
        ui.text(
            "br-feas-score",
            f"**Feasibility {ev.feasibility_score}/5**{feas_note}. {ev.feasibility_rationale}",
            "body",
        ),
        ui.chart("br-chart", f"{_DATA_ROOT}/chart", height=440),
    ]


def _next_steps_tab(
    ev: OpportunityEvaluation,
    pack: Pack | None,
    skipped: set[int],
    links: dict[str, str],
) -> list[ui.Component]:
    ids = ["br-next-text"]
    nodes: list[ui.Component] = [ui.text("br-next-text", f"➡️ {ev.recommended_next_step}", "body")]

    if needs_tech_review(ev):
        ids.append("br-review")
        nodes += ui.event_button(
            "br-review",
            "Start technical review",
            START_TECH_REVIEW,
            {"recordId": ev.record_id},
            primary=True,
        )
    ids.append("br-portfolio")
    nodes += ui.event_button("br-portfolio", "Open portfolio", OPEN_PORTFOLIO, {})

    folder = links.get("folder")
    if folder:
        where = ui.storage_name(folder)
        ids += ["br-open-folder", "br-open-folder-link"]
        nodes += ui.open_url_button(
            "br-open-folder",
            f"Open folder in {where}",
            folder,
            icon="folder_open",
            fallback=f"📁 [Open the opportunity folder in {where}]({folder})",
        )
        if links.get("business"):
            ids += ["br-open-brief", "br-open-brief-link"]
            nodes += ui.open_url_button(
                "br-open-brief",
                f"Open brief in {where}",
                links["business"],
                icon="description",
                fallback=f"🔗 [Open the brief file in {where}]({links['business']})",
            )

    if pack is not None:
        ids.append("br-workspace")
        nodes += ui.event_button(
            "br-workspace", "🗂️ Open workspace", OPEN_WORKSPACE, {"recordId": ev.record_id}
        )
        ids += ["br-reopen-rule", "br-reopen-caption"]
        nodes += [
            ui.divider("br-reopen-rule"),
            ui.text("br-reopen-caption", "Need to change an answer? Reopen a stage:", "caption"),
        ]
        for idx, stage in enumerate(pack.stages):
            bid = f"br-reopen-{stage.id}"
            label = (
                f"⚠ Complete skipped stage {idx + 1}: {stage.label}"
                if idx in skipped
                else f"Reopen stage {idx + 1}: {stage.label}"
            )
            ids.append(bid)
            nodes += ui.event_button(bid, label, REVISE_STAGE, {"stage": stage.id})

    nodes.insert(0, ui.column("br-next", ids))
    return nodes


def build_brief_view(
    record: UseCaseRecord,
    surface_id: str,
    *,
    pack: Pack | None = None,
    skipped_stages: set[int] | None = None,
    portfolio: list[OpportunityEvaluation] | None = None,
    links: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The full message sequence for the brief side panel.

    ``pack`` is passed only when the brief belongs to the current
    conversation; it adds the reopen-stage buttons, which act on this
    session. ``portfolio`` adds the other opportunities to the chart so the
    user sees where this one lands. ``links`` (``folder``, ``business``)
    adds buttons that open the stored files.
    """
    skipped = skipped_stages or set()
    ev = evaluate_opportunity(record, apply_to_record=False)

    from qualify.packs.loader import load_pack  # noqa: PLC0415

    open_items = _open_items(record, pack or load_pack("business"), skipped)
    section_ids, nodes = _brief_sections(record, open_items)
    nodes.insert(0, ui.column("br-brief", section_ids))
    nodes += _scores_tab(ev)
    nodes += _next_steps_tab(ev, pack, skipped, links or {})

    title = record.meta.initiative_name or "Untitled use case"
    sub_date = (record.meta.submission_date or date.today()).isoformat()
    gate = (
        "Gate 1: conditional — open items pending"
        if open_items
        else "Gate 1: ready for CoE and technical review"
    )
    hours = ev.annual_hours_saved
    nodes += [
        ui.text("br-title", f"Business Value Brief — {title}", "h3"),
        ui.text("br-meta", f"{record.meta.record_id} · {sub_date} · {gate}", "caption"),
        ui.tabs(
            "br-tabs",
            [("Brief", "br-brief"), ("Scores", "br-scores"), ("Next steps", "br-next")],
        ),
    ]
    root = ui.canvas_root(
        ["br-title", "br-meta", "br-tabs"],
        title=f"Business Value Brief — {title}",
        description=(
            f"{record.meta.record_id} · {QUADRANT_ICONS[ev.quadrant]} {ev.quadrant} · "
            + (f"{hours:,.0f} hrs/yr" if hours else "unsized")
        ),
        icon="description",
    )

    others = [p for p in (portfolio or []) if p.record_id != ev.record_id]
    points = matrix_points([*others, ev], highlight_id=ev.record_id)
    data = {"ui": {"brief": {"chart": quadrant_matrix_spec(points)}}}
    return ui.surface(surface_id, [root, *nodes], data)
