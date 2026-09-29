"""Tests for the Technical Architecture Dossier and the renderer registry.

The registry is the risky part. It is an indirection that can silently return
the wrong document, and a wrong-but-plausible document is exactly the failure
this project has been bitten by before: 283 unit tests once passed against a
completely broken UI. So these tests assert on markers that only one renderer
can produce.
"""

from __future__ import annotations

import pytest

from qualify.export import (
    DELIVERABLE_FILENAMES,
    deliverable_filename,
    render_deliverable,
)
from qualify.export.dossier import render_technical_dossier
from qualify.scoring.technical import score_technical
from tests.test_technical_scoring import empty_record, perfect_record


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_registry_returns_a_different_document_per_pack() -> None:
    """The guard against a mis-wired registry.

    If `"tech"` were pointed at `render_business_brief`, the dossier-only
    markers below would vanish and this fails. Verified by mutation.
    """
    record = perfect_record()
    record.meta.initiative_name = "Claims triage"

    business = render_deliverable("business", record)
    tech = render_deliverable("tech", record)

    assert business != tech

    # Dossier-only markers.
    assert "Technical Architecture Dossier" in tech
    assert "22-Subcriteria Technical Audit Matrix" in tech
    assert "Access Checklist" in tech
    assert "Technical Architecture Dossier" not in business

    # Brief-only marker.
    assert "Business Value Brief" in business
    assert "Business Value Brief" not in tech


def test_unknown_pack_falls_back_to_the_brief_rather_than_raising() -> None:
    """A missing renderer must not lose a completed interview."""
    out = render_deliverable("nonexistent", perfect_record())
    assert "Business Value Brief" in out


def test_each_pack_writes_a_distinct_filename() -> None:
    """Both land in one record folder, so they must not collide."""
    assert deliverable_filename("business") == "Business_Value_Brief.md"
    assert deliverable_filename("tech") == "Technical_Architecture_Dossier.md"
    assert len(set(DELIVERABLE_FILENAMES.values())) == len(DELIVERABLE_FILENAMES)
    # An unknown pack must not silently overwrite the brief... but it does,
    # by design, because the fallback renderer *is* the brief. Stated here so
    # the coupling is deliberate rather than accidental.
    assert deliverable_filename("nonexistent") == "Business_Value_Brief.md"


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_header_carries_the_score_and_the_profile() -> None:
    record = perfect_record()
    record.meta.initiative_name = "Claims triage"
    out = render_technical_dossier(record)

    assert "# Technical Architecture Dossier & Access Checklist: Claims triage [SCOPED]" in out
    assert "100% (44/44)" in out
    assert "**Key 2 Authorisation:** **APPROVED**" in out


def test_an_incomplete_review_is_a_working_draft() -> None:
    out = render_technical_dossier(empty_record())

    assert "[WORKING DRAFT]" in out
    assert "[SCOPED]" not in out
    assert "**PENDING**" in out


def test_unestablished_fields_render_as_pending_not_as_guesses() -> None:
    """The Zero Extrapolation Rule, enforced.

    An empty review must not produce a document that reads as if answers
    exist. Every unknown is a visible marker.
    """
    out = render_technical_dossier(empty_record())

    assert "⚠️ [Pending Stage 1 Discovery]" in out
    assert "⚠️ [Pending Stage 2 Discovery]" in out
    assert "⚠️ [Pending Stage 3 Discovery]" in out
    assert "⚠️ [Pending Stage 4 Discovery]" in out
    assert "⚠️ [Pending Stage 5 Discovery]" in out


def test_blockers_appear_above_the_score() -> None:
    """A reader who stops at the header must still see the blocker."""
    record = perfect_record()
    record.technical.network.transit_blocker_status = "airgapped"
    out = render_technical_dossier(record)

    blocker_pos = out.index("Critical blockers found")
    score_pos = out.index("Feasibility Score")
    assert blocker_pos < score_pos

    assert "2.4 Airgap / transit blockers" in out
    assert "Blockers / High Risk" in out


def test_audit_matrix_lists_all_22_subcriteria() -> None:
    record = perfect_record()
    out = render_technical_dossier(record)
    score = score_technical(record)

    for sub in score.subscores:
        assert f"| {sub.id} | {sub.label} |" in out, f"{sub.id} missing from matrix"

    assert out.count("✅ PASS") == 22
    assert "**Total: 44/44 — 100% technical readiness.**" in out


def test_matrix_marks_unanswered_findings_in_italics() -> None:
    """Unanswered reads differently from refused, even at the same score."""
    out = render_technical_dossier(empty_record())
    assert "*Transit path undefined.*" in out
    assert out.count("❌ FAIL") == 22


def test_systems_matrix_renders_rows_when_systems_exist() -> None:
    record = perfect_record()
    out = render_technical_dossier(record)

    assert "| Claims DB | " in out
    assert "REST API" in out
    assert "A. Rossi" in out


def test_systems_matrix_falls_back_to_phase_1_sources() -> None:
    """Before the systems extractor runs, Phase 1's list is all we have.

    It must be shown as unverified rather than omitted, or the reviewer loses
    the only inventory that exists.
    """
    record = empty_record()
    record.technical.data_sources = ["sharepoint", "salesforce"]
    out = render_technical_dossier(record)

    assert "sharepoint — ⚠️ [Pending Stage 1 Discovery]" in out
    assert "salesforce — ⚠️ [Pending Stage 1 Discovery]" in out


def test_checklist_cannot_claim_done_while_its_subcriterion_fails() -> None:
    """The checklist is derived from the score, not written separately."""
    record = perfect_record()
    record.technical.landing_zone_status = "none"
    out = render_technical_dossier(record)

    assert "- [ ] **Landing zone provisioned" in out
    assert "blocked on 5.3" in out
    # Everything else still passes, so those stay ticked.
    assert "- [x] **Network topology" in out


def test_all_checklist_items_tick_on_a_perfect_record() -> None:
    out = render_technical_dossier(perfect_record())
    assert "- [ ]" not in out


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda r: None, "Ready for Sprint #1"),
        (
            lambda r: setattr(r.technical.security, "cloud_policy_status", "banned"),
            "Resolve the blockers above",
        ),
    ],
)
def test_next_step_tells_the_reader_what_to_do(mutate, expected: str) -> None:
    record = perfect_record()
    mutate(record)
    assert expected in render_technical_dossier(record)


def test_next_step_on_an_empty_review_asks_to_finish_it() -> None:
    out = render_technical_dossier(empty_record())
    assert "Finish the review" in out
    assert "22 subcriteria have not been discussed" in out


def test_score_can_be_injected_so_one_turn_reports_one_number() -> None:
    """The turn engine scores once and renders with the same result.

    Computing it twice invites two different numbers in the same conversation
    if anything mutates the record in between.
    """
    record = perfect_record()
    score = score_technical(record)
    record.technical.security.cloud_policy_status = "banned"  # would change it

    out = render_technical_dossier(record, score=score)

    assert "100% (44/44)" in out
    assert "Critical blockers found" not in out
