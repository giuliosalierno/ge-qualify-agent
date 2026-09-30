"""Tests for the UseCaseRecord contract.

These lock down the three rules that everything downstream assumes:
ownership, provenance monotonicity, and derived-value arithmetic.
"""

import pytest

from qualify.schema.capability import CapabilityLevel, DeliveryTier
from qualify.schema.use_case_record import (
    Meta,
    OwnershipError,
    UseCaseRecord,
    assert_agent_writable,
)


def make_record(**meta) -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="uc-001", **meta))


# --- ownership -------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "scoring.feasibility_score",
        "/uc/scoring/business_value_score",
        "execution.business_owner",
        "/uc/execution/target_mvp_date",
        "derived.total_annual_team_hours_saved",
    ],
)
def test_agent_cannot_write_coe_fields(path):
    with pytest.raises(OwnershipError):
        assert_agent_writable(path)


@pytest.mark.parametrize(
    "path",
    [
        "business.problem_description",
        "/uc/technical/data_sources",
        "proposed.business_owner",
        "sizing.task_frequency_weekly",
        "meta.department_bu",
    ],
)
def test_agent_can_write_green_fields(path):
    assert_agent_writable(path)


def test_volunteered_owner_goes_to_proposed_not_execution():
    """The user naming a business owner must not populate the CoE field."""
    r = make_record()
    r.proposed.business_owner = "A. Rossi"

    assert r.proposed.business_owner == "A. Rossi"
    assert r.execution.business_owner is None


# --- provenance ------------------------------------------------------------


def test_provenance_defaults_to_empty():
    assert make_record().provenance_of("business.problem_description") == "empty"


def test_agent_draft_cannot_overwrite_user_confirmed():
    """The extractor re-deriving an old value must not undo a manual fix."""
    r = make_record()
    r.mark("business.user_profile", "agent_draft")
    r.mark("business.user_profile", "user_confirmed")
    r.mark("business.user_profile", "agent_draft")

    assert r.provenance_of("business.user_profile") == "user_confirmed"


def test_user_confirmed_always_wins():
    r = make_record()
    r.mark("business.user_profile", "agent_draft")
    r.mark("business.user_profile", "user_confirmed")

    assert r.provenance_of("business.user_profile") == "user_confirmed"


def test_unconfirmed_paths_drives_stage_gating():
    r = make_record()
    paths = ["business.problem_description", "business.user_profile"]
    r.mark("business.problem_description", "user_confirmed")
    r.mark("business.user_profile", "agent_draft")

    assert r.unconfirmed_paths(paths) == ["business.user_profile"]


# --- derived arithmetic ----------------------------------------------------


def test_hours_saved_matches_skill_md_formula():
    """10 tasks/week x 6 min saved x 20 users, per the SKILL.md formula."""
    r = make_record()
    r.business.user_count = 20
    r.sizing.task_frequency_weekly = 10
    r.sizing.target_minutes_saved_per_task = 6

    d = r.derived
    assert d.weekly_hours_saved_per_user == 1.0  # 10 * 6 / 60
    assert d.annual_hours_saved_per_user == 50.0  # * 50 weeks
    assert d.total_annual_team_hours_saved == 1000.0  # * 20 users


def test_rounding_does_not_compound():
    """10 users x 10/wk x 1 min: 5,000 min = 83.33 h, not 85 (seen in GE)."""
    r = make_record()
    r.business.user_count = 10
    r.sizing.task_frequency_weekly = 10
    r.sizing.target_minutes_saved_per_task = 1

    d = r.derived
    assert d.weekly_hours_saved_per_user == 0.17
    assert d.annual_hours_saved_per_user == 8.33
    assert d.total_annual_team_hours_saved == 83.33


def test_missing_inputs_yield_none_not_zero():
    """Zero would read as "we measured no saving". None reads as "not sized"."""
    r = make_record()
    r.sizing.task_frequency_weekly = 10
    # target_minutes_saved_per_task deliberately absent

    assert r.derived.weekly_hours_saved_per_user is None
    assert r.derived.total_annual_team_hours_saved is None


def test_team_total_needs_user_count():
    """Per-user figures are computable without U; the team total is not."""
    r = make_record()
    r.sizing.task_frequency_weekly = 10
    r.sizing.target_minutes_saved_per_task = 6

    d = r.derived
    assert d.annual_hours_saved_per_user == 50.0
    assert d.total_annual_team_hours_saved is None


def test_derived_tracks_its_inputs():
    """Derived is recomputed on access, so it cannot go stale."""
    r = make_record()
    r.business.user_count = 10
    r.sizing.task_frequency_weekly = 10
    r.sizing.target_minutes_saved_per_task = 6
    assert r.derived.total_annual_team_hours_saved == 500.0

    r.business.user_count = 20
    assert r.derived.total_annual_team_hours_saved == 1000.0


# --- capability ladder -----------------------------------------------------


def test_citizen_builder_boundary_is_four():
    """docs/framework.md L111: "citizen builder opportunities (Tiers 1-4)"."""
    for level in CapabilityLevel:
        assert level.is_citizen_builder == (level <= 4)


def test_delivery_tier_is_derived_from_capability():
    r = make_record()
    r.technical.capability_level = CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT

    assert r.derived.delivery_tier == DeliveryTier.LOW_CODE


def test_every_level_maps_to_a_tier_and_labels():
    for level in CapabilityLevel:
        assert isinstance(level.delivery_tier, DeliveryTier)
        assert level.label
        assert level.legacy_label


def test_legacy_labels_cover_agent_plan_wording():
    """A reader of docs/framework.md L61 must be able to find each level."""
    legacy = {lvl.legacy_label for lvl in CapabilityLevel}

    assert "Gemini Spark" in legacy
    assert "Workflow Builder with custom MCP" in legacy


# --- serialisation ---------------------------------------------------------


def test_round_trips_through_json():
    r = make_record(initiative_name="Ticket triage", department_bu="Support")
    r.business.problem_description = "Manual triage across three systems"
    r.technical.capability_level = CapabilityLevel.CUSTOM_SKILL
    r.mark("business.problem_description", "user_confirmed")

    restored = UseCaseRecord.model_validate_json(r.model_dump_json())

    assert restored.meta.initiative_name == "Ticket triage"
    assert restored.business.problem_description == r.business.problem_description
    assert restored.technical.capability_level == CapabilityLevel.CUSTOM_SKILL
    assert restored.provenance_of("business.problem_description") == "user_confirmed"


def test_derived_is_serialised_for_the_data_model():
    """The surface binds to derived paths, so they must appear in the dump."""
    r = make_record()
    r.business.user_count = 5
    r.sizing.task_frequency_weekly = 4
    r.sizing.target_minutes_saved_per_task = 15

    dumped = r.model_dump()
    assert dumped["derived"]["total_annual_team_hours_saved"] == 250.0


def test_unknown_fields_are_rejected():
    """A typo in a pack or patch must fail loudly, not vanish."""
    with pytest.raises(Exception):
        UseCaseRecord.model_validate(
            {"meta": {"record_id": "uc-001"}, "buisness": {}}
        )
