"""Markdown Business Value Brief generator.

Implements Section 8.1 of the design plan and Decision D8:
- During the interview, only the A2UI living form is shown.
- Upon completion of Stage 4 (Gate 1), the agent emits a structured Markdown
  Business Value Brief ready for Gemini Enterprise Canvas or Google Docs export.

Written for business owners and sponsors: one page, plain language, no
architecture terms. The technical design lives in the Technical Architecture
Dossier. The same markdown is shown in chat, in the workspace side panel and
in the exported file, so it sticks to what all three render: headings,
bullets, bold/italic and plain text (no LaTeX, no wide tables).
"""

from __future__ import annotations

from datetime import date

from qualify.schema.use_case_record import WORK_WEEKS_PER_YEAR, UseCaseRecord
from qualify.scoring.business_tier import (
    approach_label,
    business_summary,
    classify_capability,
)
from qualify.scoring.connectors import resolve

_CLASSIFICATION = {
    "public": "Public",
    "internal": "Internal",
    "confidential": "Confidential",
    "restricted": "Restricted",
}


def _num(value: float) -> str:
    return f"{value:,.0f}" if value >= 10 else f"{value:,.1f}".rstrip("0").rstrip(".")


def _systems(record: UseCaseRecord) -> str:
    tech = record.technical
    names = [
        resolve(s).label
        for s in tech.data_sources
        if s.strip() and s.lower() not in ("other", "unknown")
    ]
    if tech.other_data_sources:
        names.append(tech.other_data_sources.strip())
    if any(s.lower() == "unknown" for s in tech.data_sources):
        names.append("others not confirmed yet")
    return ", ".join(dict.fromkeys(names)) or "None named yet"


def _value_section(record: UseCaseRecord) -> list[str]:
    biz, sizing, derived = record.business, record.sizing, record.derived
    total = derived.total_annual_team_hours_saved
    users = biz.user_count
    freq = sizing.task_frequency_weekly
    saved = sizing.target_minutes_saved_per_task
    base = sizing.baseline_minutes_per_task

    if not total:
        missing = [
            label
            for label, v in (
                ("number of users", users),
                ("how often the task runs", freq),
                ("minutes saved each time", saved),
            )
            if not v
        ]
        return [
            "## Value",
            "",
            "Not sized yet. To estimate hours saved we still need: "
            + ", ".join(missing or ["the sizing inputs"])
            + ".",
            "",
        ]

    lines = [
        f"## Value: {_num(total)} hours a year",
        "",
        f"{users:,} users × {_num(freq)} times a week × {_num(saved)} min saved × "
        f"{WORK_WEEKS_PER_YEAR} weeks = **{_num(total)} hours a year** "
        f"(about {_num(derived.annual_hours_saved_per_user or 0)} per person).",
    ]
    if base:
        pct = round(100 * saved / base)
        lines.append("")
        lines.append(
            f"Today the task takes {_num(base)} min; the target saves {_num(saved)} "
            f"min each time ({pct}% faster)."
        )
    lines.append("")
    return lines


def render_business_brief(
    record: UseCaseRecord, skipped_stages: set[int] | None = None
) -> str:
    """Renders the Business Value Brief markdown for a UseCaseRecord."""
    from qualify.a2ui.provenance import missing_required  # noqa: PLC0415
    from qualify.packs.loader import load_pack  # noqa: PLC0415

    classify_capability(record)
    pack = load_pack("business")
    skipped_set = skipped_stages or set()

    open_items: list[tuple[str, list[str]]] = []
    for idx, st in enumerate(pack.stages):
        missing = missing_required(record, st)
        if missing or idx in skipped_set:
            labels = [f.label for f in missing] or ["Pending confirmation"]
            open_items.append((st.label, labels))

    meta, biz, prop, tech = record.meta, record.business, record.proposed, record.technical
    title = meta.initiative_name or "Untitled use case"
    sub_date = (meta.submission_date or date.today()).isoformat()
    owner = prop.business_owner or meta.submitter or "Not named yet"
    sponsor = prop.executive_sponsor or "Not confirmed yet"
    total = record.derived.total_annual_team_hours_saved
    open_count = sum(len(labels) for _, labels in open_items)

    if open_items:
        status = (
            f"⚠️ Conditional qualification — {open_count} open "
            f"item{'s' if open_count != 1 else ''} to confirm"
        )
    else:
        status = "✅ Business qualification complete — ready for CoE review"

    users = (
        f"{biz.user_count:,}" + (f" · {biz.user_profile}" if biz.user_profile else "")
        if biz.user_count
        else (biz.user_profile or "Not sized yet")
    )
    classification = _CLASSIFICATION.get(
        (tech.security.data_classification or "").lower(), "Not specified yet"
    )

    lines = [
        f"# Business Value Brief: {title}",
        "",
        f"**{status}** · {meta.record_id} · {sub_date}",
        "",
        "## Summary",
        "",
        f"- **Team:** {meta.department_bu or 'Not specified'}",
        f"- **Sponsor:** {sponsor} · **Business owner:** {owner}",
        f"- **Users:** {users}",
        f"- **Time saved:** {f'**{_num(total)} hours a year**' if total else 'Not sized yet'}",
        f"- **Recommended approach:** {approach_label(tech.capability_level)}",
        "",
        "## The problem",
        "",
        biz.problem_description or "_No problem description recorded yet._",
        "",
    ]
    if biz.user_stories:
        lines += [f"**User story:** {biz.user_stories}", ""]
    if biz.expected_impacts:
        lines += [f"**Expected impact:** {biz.expected_impacts}", ""]

    lines += _value_section(record)

    lines += [
        "## Systems and data",
        "",
        f"- **Systems involved:** {_systems(record)}",
        f"- **Data classification:** {classification}",
        "",
        "## Recommended approach",
        "",
        business_summary(record),
        "",
        "_The technical design (architecture, connectors, security, deployment) "
        "is worked out in the technical review._",
        "",
    ]

    if open_items:
        lines += [
            "## Open items",
            "",
            "Skipped during the intake; confirm these before sign-off:",
            "",
        ]
        lines += [f"- **{st}:** {', '.join(labels)}" for st, labels in open_items]
        lines.append("")

    lines += [
        "## What happens next",
        "",
        "1. **Sponsor sign-off:** send this brief to the executive sponsor to confirm the priority.",
        "2. **Portfolio review:** the AI CoE ranks this opportunity against the others on value and feasibility.",
        f"3. **Technical review:** if systems need connecting, a Solution Architect checks them by typing `technical review {meta.record_id}`.",
    ]
    return "\n".join(lines)
