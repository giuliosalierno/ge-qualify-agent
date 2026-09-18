"""Activity 3: Portfolio Prioritization & Value/Feasibility Scoring Engine.

Evaluates a collection of qualified `UseCaseRecord` items across business units,
computing independent **Business Value (1–5)** and **Technical Feasibility (1–5)**
scores and segmenting the portfolio into the four `AGENT_PLAN.MD` (§Activity 3)
quadrants:

1. **Quick Wins** — High Business Value (>=3), High Feasibility (>=4), no hard blockers.
2. **Strategic Bets** — High Business Value (>=3), Moderate/Complex Feasibility (2–3), no hard blockers.
3. **Departmental Niche** — Localized Business Value (<3), High Feasibility (>=4), no hard blockers.
4. **Deprioritized** — Hard technical blockers (Subcriteria 2.4 / 3.5) or low value + low feasibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import UseCaseRecord
from qualify.scoring.business_tier import classify_capability
from qualify.scoring.technical import TechnicalScore, score_technical

QuadrantName = Literal[
    "Quick Wins",
    "Strategic Bets",
    "Departmental Niche",
    "Deprioritized",
]

QUADRANT_ORDER: tuple[QuadrantName, ...] = (
    "Quick Wins",
    "Strategic Bets",
    "Departmental Niche",
    "Deprioritized",
)


@dataclass(frozen=True)
class OpportunityEvaluation:
    """Complete Activity 3 portfolio scoring output for a single opportunity."""

    record: UseCaseRecord
    record_id: str
    initiative_name: str
    department_bu: str
    business_value_score: int
    feasibility_score: int
    composite_rank_score: float
    quadrant: QuadrantName
    priority_status: str
    annual_hours_saved: float | None
    user_count: int | None
    capability_level: CapabilityLevel
    has_brief: bool
    has_dossier: bool
    is_indicative_feasibility: bool
    readiness_pct: int | None
    has_hard_blocker: bool
    blocker_summary: str | None
    value_rationale: str
    feasibility_rationale: str
    recommended_next_step: str
    folder_url: str | None = None


@dataclass(frozen=True)
class PortfolioSummary:
    """Aggregated portfolio evaluation across all qualified opportunities."""

    evaluations: tuple[OpportunityEvaluation, ...]
    total_count: int
    total_annual_hours_saved: float
    total_users_reached: int
    pending_tech_review_count: int
    key2_ready_count: int
    blocked_count: int

    def by_quadrant(self) -> dict[QuadrantName, list[OpportunityEvaluation]]:
        grouped: dict[QuadrantName, list[OpportunityEvaluation]] = {
            q: [] for q in QUADRANT_ORDER
        }
        for ev in self.evaluations:
            grouped[ev.quadrant].append(ev)
        return grouped


def _score_business_value(record: UseCaseRecord) -> tuple[int, float, str]:
    """Computes integer (1–5), continuous (1.0–5.0) business value score, and rationale."""
    hours = record.derived.total_annual_team_hours_saved
    users = record.business.user_count
    impacts = (record.business.expected_impacts or "").strip()

    notes: list[str] = []
    if hours is not None and hours > 0:
        if hours >= 10_000:
            raw = 5.0
        elif hours >= 2_500:
            raw = 4.0 + min(0.8, (hours - 2_500) / 10_000)
        elif hours >= 500:
            raw = 3.0 + (hours - 500) / 2_500
        elif hours >= 100:
            raw = 2.0 + (hours - 100) / 500
        else:
            raw = 1.2 + (hours / 125)
        notes.append(f"{hours:,.0f} hrs/yr saved")
    elif users is not None and users > 0:
        if users >= 500:
            raw = 4.0
        elif users >= 100:
            raw = 3.2
        elif users >= 25:
            raw = 2.2
        else:
            raw = 1.5
        notes.append(f"{users:,} users (hours unsized)")
    else:
        raw = 1.5 if len(impacts) > 40 else 1.0
        notes.append("sizing inputs incomplete")

    if users is not None and users >= 250 and hours is not None:
        raw = min(5.0, raw + 0.4)
        notes.append(f"{users:,} users")
    elif users is not None and users > 0 and hours is not None:
        notes.append(f"{users:,} users")

    if len(impacts) >= 60:
        raw = min(5.0, raw + 0.2)

    score_int = max(1, min(5, int(round(raw))))
    return score_int, round(raw, 2), ", ".join(notes)


def _has_technical_review_data(record: UseCaseRecord, tech_score: TechnicalScore, has_dossier: bool) -> bool:
    """Returns True if Phase 2 (Technical Architecture Review) subcriteria have been populated."""
    answered_count = sum(1 for s in tech_score.subscores if s.answered)
    return answered_count >= 5


def _score_feasibility(
    record: UseCaseRecord,
    *,
    has_dossier: bool = False,
) -> tuple[int, float, bool, int | None, bool, str | None, str]:
    """Computes feasibility (1–5), continuous (1.0–5.0), indicative flag, readiness %, blocker state, and rationale."""
    classify_capability(record)
    level = record.technical.capability_level or CapabilityLevel.DEFAULT_ASSISTANT
    tech_score = score_technical(record)
    has_tech_data = _has_technical_review_data(record, tech_score, has_dossier)

    if tech_score.blockers:
        blocker_labels = ", ".join(f"{b.id} {b.label}" for b in tech_score.blockers)
        return (
            1,
            1.0,
            False,
            tech_score.readiness_pct,
            True,
            blocker_labels,
            f"Hard Blocker ({blocker_labels})",
        )

    if has_tech_data:
        pct = tech_score.readiness_pct
        if pct >= 85:
            raw = 4.6 + (pct - 85) / 37.5
            score_int = 5
        elif pct >= 70:
            raw = 3.8 + (pct - 70) / 20.0
            score_int = 4
        elif pct >= 50:
            raw = 2.8 + (pct - 50) / 25.0
            score_int = 3
        elif pct >= 30:
            raw = 1.8 + (pct - 30) / 25.0
            score_int = 2
        else:
            raw = max(1.0, pct / 20.0)
            score_int = 1
        gate_note = "Key 2 Cleared" if tech_score.key2_ready else f"{pct}% tech readiness"
        return (
            score_int,
            round(min(5.0, raw), 2),
            False,
            pct,
            False,
            None,
            f"{gate_note} ({level.label})",
        )

    # Indicative scoring from Phase 1 capability level
    tier_map: dict[CapabilityLevel, tuple[int, float]] = {
        CapabilityLevel.DEFAULT_ASSISTANT: (5, 4.8),
        CapabilityLevel.CUSTOM_SKILL: (5, 4.6),
        CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT: (4, 4.1),
        CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT: (4, 3.8),
        CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP: (3, 3.0),
        CapabilityLevel.HIGH_CODE_AGENT: (2, 2.3),
    }
    score_int, raw = tier_map.get(level, (3, 3.0))
    return (
        score_int,
        raw,
        True,
        None,
        False,
        None,
        f"Indicative from {level.label} (pending Gate 2 review)",
    )


def _assign_quadrant(
    value_score: int,
    feasibility_score: int,
    has_hard_blocker: bool,
) -> QuadrantName:
    """Assigns one of the four AGENT_PLAN.MD portfolio quadrants."""
    if has_hard_blocker:
        return "Deprioritized"
    if value_score >= 3 and feasibility_score >= 4:
        return "Quick Wins"
    if value_score >= 3 and feasibility_score >= 2:
        return "Strategic Bets"
    if value_score < 3 and feasibility_score >= 4:
        return "Departmental Niche"
    return "Deprioritized"


def _recommended_next_step(
    quadrant: QuadrantName,
    level: CapabilityLevel,
    has_dossier: bool,
    has_hard_blocker: bool,
    blocker_summary: str | None,
    record_id: str,
) -> str:
    if has_hard_blocker:
        return f"Hold / Escalate blocker ({blocker_summary}) with Security & Network Governance."
    if quadrant == "Quick Wins":
        if level in (
            CapabilityLevel.DEFAULT_ASSISTANT,
            CapabilityLevel.CUSTOM_SKILL,
            CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,
            CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT,
        ):
            return f"Enable citizen-builder delivery via {level.label} immediately."
        if not has_dossier:
            return f"Fast-track Technical Review (`technical review {record_id}`) to unlock build."
        return "Cleared for immediate sprint execution."
    if quadrant == "Strategic Bets":
        if not has_dossier:
            return f"Run Phase 2 Technical Architecture Review (`technical review {record_id}`)."
        return "Schedule Activity 4 High-Code Scoping Workshop with Lead Solution Architect."
    if quadrant == "Departmental Niche":
        return f"Delegate to department champion for self-service configuration ({level.label})."
    return "Keep in backlog; re-evaluate during Activity 5 quarterly GE feature review."


def evaluate_opportunity(
    record: UseCaseRecord,
    *,
    has_brief: bool = True,
    has_dossier: bool = False,
    folder_url: str | None = None,
    apply_to_record: bool = True,
) -> OpportunityEvaluation:
    """Scores a single `UseCaseRecord` and optionally updates `record.scoring`."""
    classify_capability(record)
    level = record.technical.capability_level or CapabilityLevel.DEFAULT_ASSISTANT

    val_int, val_raw, val_rationale = _score_business_value(record)
    (
        feas_int,
        feas_raw,
        is_indicative,
        readiness_pct,
        has_blocker,
        blocker_summary,
        feas_rationale,
    ) = _score_feasibility(record, has_dossier=has_dossier)

    quadrant = _assign_quadrant(val_int, feas_int, has_blocker)
    if has_blocker:
        priority_status = "Blocked"
    elif has_dossier and readiness_pct is not None and readiness_pct >= 80:
        priority_status = "Scoped (Key 2 Cleared)"
    elif has_dossier:
        priority_status = f"Tech Reviewed ({readiness_pct}%)"
    elif level in (
        CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP,
        CapabilityLevel.HIGH_CODE_AGENT,
    ):
        priority_status = "Pending Tech Review"
    else:
        priority_status = "Qualified (Citizen Ready)"

    if apply_to_record:
        record.scoring.business_value_score = val_int
        record.scoring.feasibility_score = feas_int
        record.scoring.category = quadrant
        record.scoring.priority_status = priority_status

    composite = round((val_raw * 0.6) + (feas_raw * 0.4), 2)
    next_step = _recommended_next_step(
        quadrant,
        level,
        has_dossier,
        has_blocker,
        blocker_summary,
        record.meta.record_id,
    )

    return OpportunityEvaluation(
        record=record,
        record_id=record.meta.record_id,
        initiative_name=(record.meta.initiative_name or "Untitled Initiative").strip(),
        department_bu=(record.meta.department_bu or "Unspecified BU").strip(),
        business_value_score=val_int,
        feasibility_score=feas_int,
        composite_rank_score=composite,
        quadrant=quadrant,
        priority_status=priority_status,
        annual_hours_saved=record.derived.total_annual_team_hours_saved,
        user_count=record.business.user_count,
        capability_level=level,
        has_brief=has_brief,
        has_dossier=has_dossier,
        is_indicative_feasibility=is_indicative,
        readiness_pct=readiness_pct,
        has_hard_blocker=has_blocker,
        blocker_summary=blocker_summary,
        value_rationale=val_rationale,
        feasibility_rationale=feas_rationale,
        recommended_next_step=next_step,
        folder_url=folder_url,
    )


def evaluate_portfolio(
    items: list[tuple[UseCaseRecord, dict[str, object]]],
) -> PortfolioSummary:
    """Evaluates and ranks a list of `(record, metadata_dict)` pairs.

    `metadata_dict` may contain `hasBrief`, `hasDossier`, and `webUrl` from
    `SharePointConnector.list_opportunities()`.
    """
    evaluations: list[OpportunityEvaluation] = []
    for rec, meta in items:
        has_brief = bool(meta.get("hasBrief", True))
        has_dossier = bool(meta.get("hasDossier", False))
        web_url = meta.get("webUrl")
        folder_url = str(web_url) if isinstance(web_url, str) and web_url else None
        evaluations.append(
            evaluate_opportunity(
                rec,
                has_brief=has_brief,
                has_dossier=has_dossier,
                folder_url=folder_url,
                apply_to_record=True,
            )
        )

    # Sort by quadrant priority, then descending composite rank score, then hours saved
    quadrant_rank = {q: idx for idx, q in enumerate(QUADRANT_ORDER)}
    evaluations.sort(
        key=lambda e: (
            quadrant_rank.get(e.quadrant, 99),
            -e.composite_rank_score,
            -(e.annual_hours_saved or 0.0),
            e.record_id,
        )
    )

    total_hours = round(
        sum(e.annual_hours_saved or 0.0 for e in evaluations), 1
    )
    total_users = sum(e.user_count or 0 for e in evaluations)
    pending_tech = sum(1 for e in evaluations if e.has_brief and not e.has_dossier)
    key2_ready = sum(
        1
        for e in evaluations
        if not e.has_hard_blocker
        and e.readiness_pct is not None
        and e.readiness_pct >= 80
    )
    blocked = sum(1 for e in evaluations if e.has_hard_blocker)

    return PortfolioSummary(
        evaluations=tuple(evaluations),
        total_count=len(evaluations),
        total_annual_hours_saved=total_hours,
        total_users_reached=total_users,
        pending_tech_review_count=pending_tech,
        key2_ready_count=key2_ready,
        blocked_count=blocked,
    )
