"""The UseCaseRecord — the single canonical record for one use case.

This is frozen contract 4.1. Everything downstream depends on it.

Two ideas drive the shape.

1. The record IS the A2UI data model.
   Every field's JSON Pointer path matches its position in the surface's data
   model, so `updateDataModel` patches and record writes address the same
   tree. `/uc/business/problem_description` means the same thing to the
   compiler, the renderer and the record store. There is no mapping layer to
   keep in sync.

2. Ownership is structural, not conventional.
   AGENT_PLAN.MD L144-146 splits every inventory field between the Discovery
   Agent (green) and the CoE's Analysis Agent (blue). That split is enforced
   here by putting CoE fields in their own models and refusing agent writes,
   rather than relying on anyone remembering the rule.

Where the two collide — the user volunteers a business owner, but business
owner is CoE-owned — the value lands in `proposed`, never in `execution`.
See doc field_reconciliation.md 2.
"""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from qualify.schema.capability import CapabilityLevel, DeliveryTier

SCHEMA_VERSION = "1.0"

# Work weeks per year, used for annualising hours saved. Matches the constant
# already embedded in ge_intake_business/SKILL.md so existing briefs and new
# records produce identical numbers.
WORK_WEEKS_PER_YEAR = 50

Provenance = Literal["empty", "agent_draft", "user_confirmed"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# Green — collected by the Discovery Agent
# ---------------------------------------------------------------------------


class Meta(_Base):
    """Identity and submission metadata."""

    record_id: str
    # GE opens a new taskId per chat message but holds contextId stable across
    # the conversation, including browser reloads (D15, verified in Phase 0).
    # This is the only durable handle on an in-progress interview.
    context_id: str | None = None
    initiative_name: str | None = None
    submission_date: date | None = None
    submitter: str | None = None
    department_bu: str | None = None
    schema_version: str = SCHEMA_VERSION


class Business(_Base):
    """AGENT_PLAN.MD "Business needs" — all green."""

    user_profile: str | None = None
    user_count: Annotated[int | None, Field(ge=0)] = None
    problem_description: str | None = None
    user_stories: str | None = None
    expected_impacts: str | None = None


class Sizing(_Base):
    """Inputs to the hours-saved calculation.

    The inventory has no columns for these — it wants only the annual total.
    We keep them anyway. A total with no inputs is unauditable: a sponsor who
    asks "where did 4,200 hours come from?" deserves an answer, and the CoE
    needs them to sanity-check the Business Value score in Activity 3.
    """

    task_frequency_weekly: Annotated[float | None, Field(ge=0)] = None
    baseline_minutes_per_task: Annotated[float | None, Field(ge=0)] = None
    target_minutes_saved_per_task: Annotated[float | None, Field(ge=0)] = None


class SystemEntry(_Base):
    """One row of the ge_tech_review Systems & Data Landscape Matrix."""

    name: str
    function: str | None = None
    hosting_location: str | None = None
    interface: str | None = None
    data_format: str | None = None
    schema_status: str | None = None
    system_owner: str | None = None


class Network(_Base):
    hosting_environments: str | None = None
    transit_path: str | None = None
    firewall_proxy_status: str | None = None


class Security(_Base):
    user_authentication: str | None = None
    service_authentication: str | None = None
    iam_least_privilege: str | None = None
    data_classification: str | None = None


class Grounding(_Base):
    acl_preservation_required: bool | None = None
    model_profile: str | None = None


class Technical(_Base):
    """AGENT_PLAN.MD "Technical aspects", plus the tech review's detail.

    `data_sources` is the flat green inventory field. `systems` is the richer
    structure the technical review produces. The Sheet writer projects
    `systems` down to names; the record keeps both (see field_reconciliation 4).
    """

    data_sources: list[str] = Field(default_factory=list)
    systems: list[SystemEntry] = Field(default_factory=list)
    capability_level: CapabilityLevel | None = None
    capability_rationale: str | None = None
    network: Network = Field(default_factory=Network)
    security: Security = Field(default_factory=Security)
    grounding: Grounding = Field(default_factory=Grounding)


class Proposed(_Base):
    """What the user said about CoE-owned fields.

    AGENT_PLAN.MD assigns owners, sponsors and scores to the CoE. But the
    submitter is often the business owner, or knows the sponsor, and asking
    costs one turn while recovering it later costs a follow-up email per use
    case.

    So the agent asks, and records the answer here. These are proposals, not
    decisions. The Sheet shows them only when the authoritative CoE field is
    empty, flagged as unconfirmed.
    """

    business_owner: str | None = None
    executive_sponsor: str | None = None
    production_catcher_team: str | None = None
    tech_owner: str | None = None
    network_security_lead: str | None = None
    domain_sme: str | None = None
    adoption_kpi: str | None = None


# ---------------------------------------------------------------------------
# Blue — CoE only. The Discovery Agent must never write these.
# ---------------------------------------------------------------------------


class Scoring(_Base):
    """AGENT_PLAN.MD Activity 3 outputs. Analysis Agent territory.

    Both scores are 1-5 per L75. The technical review's 0-100% readiness
    figure is NOT the feasibility score — it is advisory input, and lives in
    `Derived.technical_readiness_pct`.
    """

    feasibility_score: Annotated[int | None, Field(ge=1, le=5)] = None
    business_value_score: Annotated[int | None, Field(ge=1, le=5)] = None
    priority_status: str | None = None
    category: str | None = None
    # Produced by the Activity 4 design workshop using the High-Code Scoping
    # Template, not by either agent.
    scoping_document_url: str | None = None


class Execution(_Base):
    """AGENT_PLAN.MD "Execution" — all blue."""

    business_owner: str | None = None
    executive_sponsor: str | None = None
    tech_owner: str | None = None
    target_mvp_date: date | None = None
    production_catcher_team: str | None = None


# ---------------------------------------------------------------------------
# Derived — computed, never assigned
# ---------------------------------------------------------------------------


class Derived(_Base):
    """Values computed from other fields.

    Nothing writes these directly. They exist as a model so the compiler can
    bind them into the surface and the user can watch them update live as the
    sizing inputs are filled in.
    """

    weekly_hours_saved_per_user: float | None = None
    annual_hours_saved_per_user: float | None = None
    total_annual_team_hours_saved: float | None = None
    technical_readiness_pct: Annotated[int | None, Field(ge=0, le=100)] = None
    delivery_tier: DeliveryTier | None = None


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class UseCaseRecord(_Base):
    """One use case, from first utterance to CoE prioritisation."""

    meta: Meta
    business: Business = Field(default_factory=Business)
    sizing: Sizing = Field(default_factory=Sizing)
    technical: Technical = Field(default_factory=Technical)
    proposed: Proposed = Field(default_factory=Proposed)
    scoring: Scoring = Field(default_factory=Scoring)
    execution: Execution = Field(default_factory=Execution)

    # Keyed by dotted path, e.g. "business.problem_description". Absent means
    # "empty". Only fields the agent or user actually touched appear here.
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_derived(cls, data: Any) -> Any:
        """Discards any `derived` block present in the input.

        `derived` is a computed field, so it appears in `model_dump()` but is
        not accepted as input. Without this, a record could not be read back
        from its own serialised form.

        Dropping it here rather than setting `extra="ignore"` keeps the model
        strict: a typo like `buisness` still fails loudly instead of silently
        discarding a whole sub-tree.
        """
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "derived"}
        return data

    @computed_field
    @property
    def derived(self) -> Derived:
        """Recomputed on every access, so it can never drift from its inputs."""
        return compute_derived(self)


    # -- provenance -------------------------------------------------------

    def provenance_of(self, path: str) -> Provenance:
        return self.provenance.get(path, "empty")

    def mark(self, path: str, state: Provenance) -> None:
        """Records how a field came to hold its value.

        Transitions are one-way: once a human has confirmed a value,
        `agent_draft` must not overwrite it. The extractor can re-derive an
        old value from earlier conversation, and without this guard it would
        silently undo a manual correction.
        """
        if self.provenance_of(path) == "user_confirmed" and state == "agent_draft":
            return
        self.provenance[path] = state

    def unconfirmed_paths(self, paths: list[str]) -> list[str]:
        """Which of `paths` are not yet user-confirmed.

        Stage gating calls this: no stage advances while it returns non-empty.
        """
        return [p for p in paths if self.provenance_of(p) != "user_confirmed"]


def compute_derived(record: UseCaseRecord) -> Derived:
    """Derives every computed value from the record.

    Kept as a free function rather than inline so the arithmetic is directly
    unit-testable without constructing surfaces or agents.

    The formula is lifted verbatim from ge_intake_business/SKILL.md so that
    records and existing Canvas briefs agree:

        weekly minutes saved per user = T * S
        weekly hours saved per user   = that / 60
        annual hours saved per user   = that * 50 work weeks
        total annual team hours saved = that * U

    Returns Nones rather than zeros when inputs are missing. A zero would read
    as "we measured no saving"; None reads as "not yet sized", which is what
    the Zero Extrapolation Rule requires.
    """
    d = Derived()

    freq = record.sizing.task_frequency_weekly
    saved = record.sizing.target_minutes_saved_per_task
    users = record.business.user_count

    if freq is not None and saved is not None:
        d.weekly_hours_saved_per_user = round(freq * saved / 60, 2)
        d.annual_hours_saved_per_user = round(
            d.weekly_hours_saved_per_user * WORK_WEEKS_PER_YEAR, 2
        )
        if users is not None:
            d.total_annual_team_hours_saved = round(
                d.annual_hours_saved_per_user * users, 2
            )

    if record.technical.capability_level is not None:
        d.delivery_tier = record.technical.capability_level.delivery_tier

    return d


# ---------------------------------------------------------------------------
# Ownership enforcement
# ---------------------------------------------------------------------------

#: Sub-trees the Discovery Agent must never write. AGENT_PLAN.MD L144-146.
COE_OWNED_ROOTS = ("scoring", "execution", "derived")

#: A2UI data model paths are rooted at the record, e.g. `/uc/business/problem`.
#: The record's own field names start one level below that.
DATA_MODEL_ROOT = "uc"


class OwnershipError(RuntimeError):
    """Raised when agent-side code tries to write a CoE-owned field."""


def top_level_field(path: str) -> str:
    """Returns the record's top-level field name for a path.

    Accepts both spellings used in this codebase, which address the same tree:

        "/uc/scoring/feasibility_score"  -> "scoring"   (A2UI JSON Pointer)
        "scoring.feasibility_score"      -> "scoring"   (provenance key)

    The `/uc` prefix is stripped because it names the data model root, not a
    field. Missing that distinction makes every pointer look like it lives in
    a tree called "uc", which is how the ownership guard silently passed every
    CoE write in its first version.
    """
    segments = [s for s in path.replace(".", "/").split("/") if s]
    if segments and segments[0] == DATA_MODEL_ROOT:
        segments = segments[1:]
    return segments[0] if segments else ""


def assert_agent_writable(path: str) -> None:
    """Guards a write path before the agent applies it.

    Called by the patcher and the action router on every inbound value. This
    is the enforcement point for implementation_plan.md 4.1 — without it, the
    rule is a comment that a future change can quietly violate.
    """
    root = top_level_field(path)
    if root in COE_OWNED_ROOTS:
        raise OwnershipError(
            f"{path!r} is in the CoE-owned '{root}' tree. "
            f"The Discovery Agent must not write it. "
            f"If the user volunteered this, record it under 'proposed' instead."
        )

