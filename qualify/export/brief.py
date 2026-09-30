"""Markdown Business Value Brief & Intake Memo generator.

Implements Section 8.1 of the design plan and Decision D8:
- During the interview, only the A2UI living form is shown.
- Upon completion of Stage 4 (Gate 1), the agent emits a structured Markdown
  Business Value Brief ready for Gemini Enterprise Canvas or Google Docs export.
"""

from __future__ import annotations

from datetime import date
from qualify.scoring.business_tier import classify_capability
from qualify.schema.use_case_record import UseCaseRecord, WORK_WEEKS_PER_YEAR


def render_business_brief(
    record: UseCaseRecord, skipped_stages: set[int] | None = None
) -> str:
    """Renders a complete Business Value Brief Markdown document from a UseCaseRecord."""
    from qualify.a2ui.provenance import missing_required  # noqa: PLC0415
    from qualify.packs.loader import load_pack  # noqa: PLC0415

    classify_capability(record)
    pack = load_pack("business")
    skipped_set = skipped_stages or set()

    open_stage_items: list[tuple[str, list[str]]] = []
    for idx, st in enumerate(pack.stages):
        missing = missing_required(record, st)
        if missing or idx in skipped_set:
            labels = [f.label for f in missing] or ["Pending confirmation"]
            open_stage_items.append((st.label, labels))

    meta = record.meta
    biz = record.business
    sizing = record.sizing
    tech = record.technical
    prop = record.proposed
    derived = record.derived

    title = meta.initiative_name or "Untitled Use Case"
    sub_date = (meta.submission_date or date.today()).isoformat()
    dept = meta.department_bu or "Unspecified"
    submitter = meta.submitter or prop.business_owner or "Unspecified"
    sponsor = prop.executive_sponsor or "Pending Sponsor Confirmation"
    biz_owner = prop.business_owner or submitter

    # Sizing figures
    u_count = biz.user_count if biz.user_count is not None else 0
    freq = sizing.task_frequency_weekly if sizing.task_frequency_weekly is not None else 0.0
    base_min = sizing.baseline_minutes_per_task if sizing.baseline_minutes_per_task is not None else 0.0
    saved_min = sizing.target_minutes_saved_per_task if sizing.target_minutes_saved_per_task is not None else 0.0

    weekly_user_hrs = derived.weekly_hours_saved_per_user or 0.0
    annual_user_hrs = derived.annual_hours_saved_per_user or 0.0
    total_team_hrs = derived.total_annual_team_hours_saved or 0.0

    # Systems & Security
    all_sources = [s for s in tech.data_sources if s != "other"]
    if tech.other_data_sources:
        all_sources.append(tech.other_data_sources)
    systems_str = ", ".join(all_sources) if all_sources else "None specified"
    classification = (tech.security.data_classification or "Unspecified").capitalize()

    # Capability & Delivery Tier
    cap_level = f"Level {tech.capability_level.value} — {tech.capability_level.label}" if tech.capability_level else "Pending Assessment"
    tier = derived.delivery_tier.label if derived.delivery_tier else "To Be Determined by CoE"
    rationale = tech.capability_rationale or _default_tier_guidance(cap_level, tier)

    gate_status = (
        "`⚠️ CONDITIONAL QUALIFICATION — OPEN DISCOVERY ITEMS PENDING`"
        if open_stage_items
        else "`BUSINESS QUALIFICATION COMPLETE — READY FOR COE & TECHNICAL REVIEW`"
    )

    lines = [
        f"# Business Value Brief: {title}",
        "",
        f"> **Gate 1 Status:** {gate_status}  ",
        f"> **Record ID:** `{meta.record_id}` | **Date:** `{sub_date}`",
        "",
    ]

    if open_stage_items:
        lines.extend([
            "---",
            "",
            "## ⚠️ Open Discovery Items (Pending Follow-Up)",
            "",
            "The following items were skipped during initial intake and should be confirmed prior to final Gate 1 sign-off:",
            "",
        ])
        for st_label, item_labels in open_stage_items:
            lines.append(f"- **{st_label}**: {', '.join(item_labels)}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 1. Executive Summary & Governance",
        "",
        "| Attribute | Value |",
        "| :--- | :--- |",
        f"| **Initiative Name** | {title} |",
        f"| **Department / BU** | {dept} |",
        f"| **Executive Sponsor** | {sponsor} |",
        f"| **Proposed Business Owner** | {biz_owner} |",
        f"| **Target User Role** | {biz.user_profile or 'Unspecified'} |",
        f"| **Impacted Headcount ($U$)** | {u_count:,} users |",
        "",
        "### As-Is Problem & Bottlenecks",
        biz.problem_description or "_No problem description recorded._",
        "",
    ])

    if biz.user_stories:
        lines.extend([
            "### User Stories",
            biz.user_stories,
            "",
        ])

    lines.extend([
        "---",
        "",
        "## 2. Value Realization & Sizing Scorecard",
        "",
        f"Calculated across **{WORK_WEEKS_PER_YEAR} work weeks/year**:",
        "",
        "| Metric | Value | Formula / Basis |",
        "| :--- | :--- | :--- |",
        f"| **Impacted Users ($U$)** | `{u_count:,}` | Confirmed headcount in role |",
        f"| **Weekly Frequency ($T$)** | `{freq:g}` / wk | Runs per user each week |",
        f"| **Baseline Duration ($M$)** | `{base_min:g}` min | Current manual time per run |",
        f"| **Target Time Saved ($S$)** | `{saved_min:g}` min | Estimated reduction per run |",
        f"| **Weekly Hours Saved / User** | `{weekly_user_hrs:,.2f}` hrs/wk | $(T \\times S) / 60$ |",
        f"| **Annual Hours Saved / User** | `{annual_user_hrs:,.0f}` hrs/yr | $\\text{{Weekly}} \\times {WORK_WEEKS_PER_YEAR}$ |",
        f"| **Total Annual Team Hours Saved** | **`{total_team_hrs:,.0f}` hrs/yr** | $U \\times \\text{{Annual Hours / User}}$ |",
        "",
    ])

    if biz.expected_impacts:
        lines.extend([
            "### Strategic & Qualitative Impacts Beyond Hours",
            biz.expected_impacts,
            "",
        ])

    lines.extend([
        "---",
        "",
        "## 3. Systems & Data Footprint",
        "",
        f"- **Systems & Repositories Touched:** {systems_str}",
        f"- **Highest Data Classification:** `{classification}`",
        "",
        "---",
        "",
        "## 4. Recommended Capability Level & Next Steps",
        "",
        f"- **GE App Capability Level:** `{cap_level}`",
        f"- **Delivery Tier (derived from the level):** `{tier}`",
        "",
        "### Implementation Guidance",
        rationale,
        "",
        "---",
        "",
        "### Next Actions for CoE Handover",
        "1. **Sponsor Sign-Off:** Forward this brief to the Executive Sponsor to confirm priority.",
        "2. **CoE Portfolio Intake:** This record (`" + meta.record_id + "`) is ready for Activity 3 Value vs. Feasibility scoring.",
        "3. **Technical Scoping (Gate 2):** For low-code/pro-code integrations, open `ge-review-tech` referencing Record ID `" + meta.record_id + "`.",
    ])

    return "\n".join(lines)


def _default_tier_guidance(cap_level: str, tier: str) -> str:
    """Provides helpful next-step guidance based on capability tier."""
    if "assistant" in cap_level.lower():
        return (
            "**Citizen Builder Path (No-Code):** This use case can be addressed immediately "
            "using Gemini Enterprise out-of-the-box assistant capabilities or custom instructions/skills. "
            "Start by creating a custom Gem/Skill with your team's standard operating instructions and sample templates."
        )
    if "workflow" in cap_level.lower() or "spark" in cap_level.lower():
        return (
            "**Low-Code / Workflow Builder Path:** This workflow spans structured steps and enterprise connectors. "
            "Prototype the flow in Gemini Enterprise Workflow Builder using authorized connectors, then validate with a small pilot group."
        )
    return (
        "**CoE / High-Code Path:** This use case involves complex multi-system orchestration or confidential data governance. "
        "Proceed to **Activity 4: Technical Architecture Review (`ge-review-tech`)** with your Solution Architect to scope connectors, IAM, and VPC transit."
    )
