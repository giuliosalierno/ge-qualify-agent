"""Tests for the 22-subcriteria technical evaluation matrix.

Two properties matter more than any individual grade:

1. **The blocker gate outranks the percentage.** A review can score 95% and
   still be impossible to build. If that ever reports as ready, the scorecard is
   worse than useless — it launders a hard failure into a green number.
2. **Unanswered reads differently from failed.** Both score 0, but the dossier
   has to say "not yet discussed" rather than accusing a system of having no
   owner.
"""

from __future__ import annotations

import pytest

from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import Meta, SystemEntry, UseCaseRecord
from qualify.scoring.technical import (
    BLOCKING_SUBCRITERIA,
    FAIL,
    KEY2_THRESHOLD,
    MAXIMUM,
    PASS,
    WARN,
    score_technical,
)


def empty_record() -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="UC-2026-EMPTY"))


def perfect_record() -> UseCaseRecord:
    """A record where all 22 subcriteria score PASS."""
    r = UseCaseRecord(meta=Meta(record_id="UC-2026-PERFECT"))
    r.technical.systems = [
        SystemEntry(
            name="Claims DB",
            hosting_location="On-prem DC, Frankfurt",
            interface="REST API",
            schema_status="OpenAPI 3.0 documented",
            system_owner="A. Rossi",
        )
    ]
    r.technical.data_freshness = "sla_documented"
    r.technical.landing_zone_status = "provisioned"
    r.technical.capability_level = CapabilityLevel.HIGH_CODE_AGENT

    r.technical.network.hosting_environments = (
        "On-premises data centre in Frankfurt plus a GCP project in europe-west3."
    )
    r.technical.network.transit_path = "ha_vpn"
    r.technical.network.firewall_proxy_status = "cleared"
    r.technical.network.transit_blocker_status = "approved"

    r.technical.security.user_authentication = "entra_id"
    r.technical.security.service_authentication = "wif"
    r.technical.security.iam_least_privilege = "read_only_scoped"
    r.technical.security.data_classification = "confidential"
    r.technical.security.residency_requirements = "eu_only"
    r.technical.security.cloud_policy_status = "authorised"

    r.technical.grounding.acl_preservation_required = True
    r.technical.grounding.citation_policy = "strict"
    r.technical.grounding.query_volume = "qps_estimated"
    r.technical.grounding.latency_sla = "sub_2s"
    r.technical.grounding.model_profile = "pro"

    r.proposed.tech_owner = "A. Rossi"
    r.proposed.network_security_lead = "B. Bianchi"
    r.proposed.domain_sme = "C. Verdi"
    return r


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_always_produces_exactly_22_subcriteria() -> None:
    for record in (empty_record(), perfect_record()):
        assert len(score_technical(record).subscores) == 22


def test_subcriterion_ids_are_unique_and_match_the_design_plan() -> None:
    ids = [s.id for s in score_technical(empty_record()).subscores]
    assert len(set(ids)) == 22
    assert ids[:5] == ["1.1", "1.2", "1.3", "1.4", "1.5"]
    assert ids[-4:] == ["5.1", "5.2", "5.3", "5.4"]


def test_maximum_is_44_points() -> None:
    assert MAXIMUM == 44
    assert score_technical(perfect_record()).maximum == 44


# ---------------------------------------------------------------------------
# The two poles
# ---------------------------------------------------------------------------


def test_empty_record_scores_zero_and_is_not_ready() -> None:
    score = score_technical(empty_record())

    assert score.earned == 0
    assert score.readiness_pct == 0
    assert score.key2_ready is False
    # Nothing was refused; it simply was not asked.
    assert len(score.unanswered) == 22


def test_perfect_record_scores_full_marks_and_clears_key_2() -> None:
    score = score_technical(perfect_record())

    failing = [(s.id, s.rationale) for s in score.subscores if s.points != PASS]
    assert not failing, f"expected all PASS, got: {failing}"
    assert score.earned == 44
    assert score.readiness_pct == 100
    assert score.blockers == ()
    assert score.key2_ready is True
    assert score.unanswered == ()


# ---------------------------------------------------------------------------
# The blocker gate — the reason this module exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_path,value,blocked_id",
    [
        ("transit_blocker_status", "airgapped", "2.4"),
        ("cloud_policy_status", "banned", "3.5"),
    ],
)
def test_a_single_blocker_denies_key_2_at_any_score(
    field_path: str, value: str, blocked_id: str
) -> None:
    """21 subcriteria at PASS must not rescue an airgap or a cloud ban.

    This is the failure the scorecard exists to prevent: a high percentage
    hiding a project that cannot be built at all.
    """
    record = perfect_record()
    if field_path == "transit_blocker_status":
        record.technical.network.transit_blocker_status = value
    else:
        record.technical.security.cloud_policy_status = value

    score = score_technical(record)

    assert score.readiness_pct >= KEY2_THRESHOLD, (
        "precondition: the score must stay high, otherwise this test would "
        "pass for the wrong reason"
    )
    assert score.key2_ready is False
    assert [s.id for s in score.blockers] == [blocked_id]
    assert score.feasibility_profile == "Blockers / High Risk"


def test_only_2_4_and_3_5_can_block() -> None:
    assert BLOCKING_SUBCRITERIA == ("2.4", "3.5")


def test_an_unasked_blocker_is_not_reported_as_a_blocker() -> None:
    """Zero Extrapolation, applied to the gate itself.

    An empty review scores 0 on 2.4 and 3.5, but nobody has claimed the network
    is airgapped — nobody has asked. Reporting that as a finding would invent
    the single fact the review exists to establish.
    """
    score = score_technical(empty_record())

    assert score.blockers == ()
    assert [s.id for s in score.unconfirmed_blockers] == ["2.4", "3.5"]


def test_an_unasked_blocker_still_denies_key_2() -> None:
    """Not asked is not the same as cleared.

    Without this, a review could reach 95% by answering everything except the
    two questions that decide the project, and be waved through.
    """
    record = perfect_record()
    record.technical.network.transit_blocker_status = None

    score = score_technical(record)

    assert score.readiness_pct >= KEY2_THRESHOLD
    assert score.blockers == ()
    assert [s.id for s in score.unconfirmed_blockers] == ["2.4"]
    assert score.key2_ready is False


@pytest.mark.parametrize(
    "field_path,blocked_id",
    [("transit_blocker_status", "2.4"), ("cloud_policy_status", "3.5")],
)
def test_not_yet_confirmed_on_a_blocker_is_unconfirmed_not_a_blocker(
    field_path: str, blocked_id: str
) -> None:
    """The pack's "Not yet confirmed" option (`unknown`) is not a finding.

    It is the honest early answer to the airgap and cloud-ban questions.
    Grading it as an answered FAIL reported a hard blocker nobody described.
    """
    record = perfect_record()
    if field_path == "transit_blocker_status":
        record.technical.network.transit_blocker_status = "unknown"
    else:
        record.technical.security.cloud_policy_status = "unknown"

    score = score_technical(record)

    assert score.blockers == ()
    assert [s.id for s in score.unconfirmed_blockers] == [blocked_id]
    assert score.key2_ready is False
    assert score.feasibility_profile != "Blockers / High Risk"


def test_not_yet_confirmed_blocker_is_worded_as_a_question_in_the_dossier() -> None:
    from qualify.export.dossier import render_technical_dossier

    record = perfect_record()
    record.technical.network.transit_blocker_status = "unknown"

    out = render_technical_dossier(record)

    assert "Critical blockers found" not in out
    assert "2.4 Airgap / transit blockers** — not yet confirmed" in out


def test_a_warn_on_a_blocking_subcriterion_also_denies_key_2() -> None:
    """An exception process in flight is not an approval."""
    record = perfect_record()
    record.technical.security.cloud_policy_status = "review_in_flight"

    score = score_technical(record)

    assert score.blockers == ()
    assert score.readiness_pct >= KEY2_THRESHOLD
    assert score.key2_ready is False


def test_a_non_blocking_failure_does_not_deny_key_2() -> None:
    """A weak answer elsewhere costs points, not the gate."""
    record = perfect_record()
    record.technical.grounding.citation_policy = "none"  # 4.2 FAIL

    score = score_technical(record)

    assert score.earned == 42
    assert score.readiness_pct == 95
    assert score.blockers == ()
    assert score.key2_ready is True


def test_score_below_threshold_denies_key_2_without_blockers() -> None:
    record = perfect_record()
    record.technical.security.service_authentication = "static_tokens"
    record.technical.security.iam_least_privilege = "admin"
    record.technical.grounding.citation_policy = "none"
    record.technical.grounding.model_profile = "undecided"
    record.technical.data_freshness = "realtime_assumed"

    score = score_technical(record)

    assert score.readiness_pct < KEY2_THRESHOLD
    assert score.blockers == ()
    assert score.key2_ready is False


# ---------------------------------------------------------------------------
# Per-subcriterion grading
# ---------------------------------------------------------------------------


def _score_for(record: UseCaseRecord, subcriterion_id: str) -> int:
    return next(
        s.points for s in score_technical(record).subscores if s.id == subcriterion_id
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("sla_documented", PASS),
        ("frequency_only", WARN),
        ("realtime_assumed", FAIL),
        ("unknown", FAIL),
    ],
)
def test_1_4_data_freshness(value: str, expected: int) -> None:
    record = perfect_record()
    record.technical.data_freshness = value
    assert _score_for(record, "1.4") == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ha_vpn", PASS),
        ("interconnect", PASS),
        ("psc", PASS),
        ("public_https", WARN),
        ("undefined", FAIL),
    ],
)
def test_2_2_transit_path(value: str, expected: int) -> None:
    record = perfect_record()
    record.technical.network.transit_path = value
    assert _score_for(record, "2.2") == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("wif", PASS),
        ("sa_keys_rotated", WARN),
        ("oauth_client", WARN),
        ("static_tokens", FAIL),
    ],
)
def test_3_2_service_authentication(value: str, expected: int) -> None:
    record = perfect_record()
    record.technical.security.service_authentication = value
    assert _score_for(record, "3.2") == expected


def test_3_4_classification_without_residency_is_a_warn() -> None:
    record = perfect_record()
    record.technical.security.residency_requirements = None
    assert _score_for(record, "3.4") == WARN


def test_3_4_unclassified_data_fails() -> None:
    record = perfect_record()
    record.technical.security.data_classification = "unclassified"
    assert _score_for(record, "3.4") == FAIL


@pytest.mark.parametrize(
    "volume,latency,expected",
    [
        ("qps_estimated", "sub_2s", PASS),
        ("qps_estimated", "sub_10s", PASS),
        ("qps_estimated", "none", WARN),
        ("rough", "sub_2s", WARN),
        ("unbounded", "sub_2s", FAIL),
    ],
)
def test_4_3_needs_both_volume_and_latency(
    volume: str, latency: str, expected: int
) -> None:
    record = perfect_record()
    record.technical.grounding.query_volume = volume
    record.technical.grounding.latency_sla = latency
    assert _score_for(record, "4.3") == expected


def test_4_1_explicit_no_is_a_real_answer() -> None:
    """"ACLs not required" is a decision, not a gap.

    `None` and `False` must not collapse together, or a reviewer who
    deliberately said no would be scored as if they had said nothing.
    """
    record = perfect_record()
    record.technical.grounding.acl_preservation_required = False
    assert _score_for(record, "4.1") == PASS

    record.technical.grounding.acl_preservation_required = None
    assert _score_for(record, "4.1") == FAIL


def test_5_1_one_lead_of_two_is_a_warn() -> None:
    record = perfect_record()
    record.proposed.network_security_lead = None
    assert _score_for(record, "5.1") == WARN


def test_5_4_flags_a_tier_that_predates_the_network_finding() -> None:
    """Phase 1 called it low-code; Phase 2 found hybrid transit."""
    record = perfect_record()
    record.technical.capability_level = CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT
    assert _score_for(record, "5.4") == WARN


# ---------------------------------------------------------------------------
# Answered versus failed
# ---------------------------------------------------------------------------


def test_unanswered_is_distinguished_from_explicitly_bad() -> None:
    unasked = perfect_record()
    unasked.technical.security.service_authentication = None

    refused = perfect_record()
    refused.technical.security.service_authentication = "static_tokens"

    unasked_sub = next(
        s for s in score_technical(unasked).subscores if s.id == "3.2"
    )
    refused_sub = next(
        s for s in score_technical(refused).subscores if s.id == "3.2"
    )

    # Same cost to the score...
    assert unasked_sub.points == refused_sub.points == FAIL
    # ...but the dossier must word them differently.
    assert unasked_sub.answered is False
    assert refused_sub.answered is True


def test_an_unrecognised_value_degrades_rather_than_crashing() -> None:
    """The pack and the grading table can drift. A live review must survive it."""
    record = perfect_record()
    record.technical.network.transit_path = "quantum_tunnel"

    score = score_technical(record)
    sub = next(s for s in score.subscores if s.id == "2.2")

    assert sub.points == FAIL
    assert "quantum_tunnel" in sub.rationale


# ---------------------------------------------------------------------------
# Feasibility profile
# ---------------------------------------------------------------------------


def test_hybrid_transit_implies_a_custom_agent() -> None:
    record = perfect_record()
    assert score_technical(record).feasibility_profile == "Custom Agent in GE App"


def test_saas_only_with_a_high_score_is_a_pure_ge_app() -> None:
    record = perfect_record()
    record.technical.network.transit_path = "public_https"
    record.technical.systems = [
        SystemEntry(
            name="Salesforce",
            hosting_location="Public SaaS",
            interface="Native managed connector",
            schema_status="Documented",
            system_owner="A. Rossi",
        )
    ]
    assert score_technical(record).feasibility_profile == "Pure GE App"


def test_grouping_preserves_the_five_dimensions() -> None:
    grouped = score_technical(perfect_record()).by_dimension()
    assert list(grouped) == [
        "1. Systems & Data",
        "2. Network Transit",
        "3. Security & IAM",
        "4. Grounding & Models",
        "5. Operational Readiness",
    ]
    assert [len(v) for v in grouped.values()] == [5, 4, 5, 4, 4]
