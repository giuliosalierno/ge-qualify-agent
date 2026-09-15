"""Tests for applying a commit payload and gating the stage.

Provenance is event-driven: it runs when the user presses Continue, and at no
other time. These tests pin the three rules that make that safe — presence in
a payload means confirmed, `user_confirmed` is write-once, and a stage does
not close while a required field is blank.

The gate matters more than usual here. The base `Button` has no `disabled`
prop (L11), so there is no client-side stage gate at all. This is the only
one.
"""

from __future__ import annotations

import pytest

from qualify.a2ui.provenance import (
    apply_commit,
    draft,
    extract_stage_values,
    missing_required,
    unconfirmed_in_stage,
)
from qualify.packs.loader import load_pack
from qualify.schema.use_case_record import Meta, UseCaseRecord

NEEDS = 0  # stage index of "needs"
SIZING = 1
DATA = 2


@pytest.fixture
def pack():
    return load_pack("business")


@pytest.fixture
def record() -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="uc-prov", context_id="ctx-1"))


def needs_payload(**overrides: object) -> dict:
    """A complete stage-1 payload, shaped the way GE actually sends it.

    The Continue button binds `{"path": "/uc"}`, so the resolved subtree
    arrives with `business` and `meta` at the top level — the `uc` segment is
    already consumed.
    """
    payload = {
        "meta": {"initiative_name": "Claims triage"},
        "business": {
            "problem_description": "Handlers re-key claims by hand.",
            "user_profile": "Claims handler",
            "user_count": "12",
        },
    }
    for dotted, value in overrides.items():
        section, _, field = dotted.partition("__")
        payload.setdefault(section, {})[field] = value
    return payload


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extract_takes_only_this_stage(pack) -> None:
    """The payload carries the whole record, including stages the user has
    not looked at since. Treating those as freshly confirmed would inflate
    confirmation into "it was on screen once"."""
    payload = needs_payload()
    payload["sizing"] = {"task_frequency_weekly": "5"}

    values = extract_stage_values(pack.stages[NEEDS], payload)

    assert "/uc/sizing/task_frequency_weekly" not in values
    assert "/uc/business/user_count" in values


def test_extract_skips_readonly_fields(pack) -> None:
    """Stage 2 holds two `/ui/summary/*` display strings. They are compiler
    output, not user input, and must never be written back."""
    payload = {"sizing": {"task_frequency_weekly": "5"}}

    values = extract_stage_values(pack.stages[SIZING], payload)

    assert not any(p.startswith("/ui/") for p in values)


def test_extract_tolerates_the_uc_root_being_present(pack) -> None:
    """Accepting both shapes means the caller need not care whether the
    binding was `/uc` or `/`."""
    wrapped = {"uc": needs_payload()}

    values = extract_stage_values(pack.stages[NEEDS], wrapped)

    assert values["/uc/business/user_profile"] == "Claims handler"


def test_absent_key_is_not_the_same_as_blank(pack) -> None:
    """A field missing from the payload was never rendered. A field present
    and empty was rendered and skipped. Only the second is a user signal."""
    payload = {"business": {"user_count": ""}}

    values = extract_stage_values(pack.stages[NEEDS], payload)

    assert values == {"/uc/business/user_count": ""}


# ---------------------------------------------------------------------------
# Committing
# ---------------------------------------------------------------------------


def test_complete_commit_passes_the_gate(pack, record) -> None:
    result = apply_commit(record, pack, NEEDS, needs_payload())

    assert result.ok, result.blocking_summary()
    assert result.stage_id == "needs"
    assert record.business.user_count == 12


def test_commit_marks_fields_user_confirmed(pack, record) -> None:
    """The user saw the value on screen and pressed Continue. That is
    confirmation, whether they typed it or the agent drafted it."""
    apply_commit(record, pack, NEEDS, needs_payload())

    assert record.provenance_of("business.user_profile") == "user_confirmed"
    assert record.provenance_of("meta.initiative_name") == "user_confirmed"


def test_blank_required_field_blocks_the_stage(pack, record) -> None:
    payload = needs_payload()
    payload["business"]["user_profile"] = ""

    result = apply_commit(record, pack, NEEDS, payload)

    assert not result.ok
    assert [f.path for f in result.missing] == ["/uc/business/user_profile"]
    assert "Who does this work" in result.blocking_summary()


def test_blank_field_is_not_confirmed(pack, record) -> None:
    """Otherwise the gate would be satisfied by the very commit that failed
    it, and the second press of Continue would sail through."""
    payload = needs_payload()
    payload["business"]["user_profile"] = ""

    apply_commit(record, pack, NEEDS, payload)

    assert record.provenance_of("business.user_profile") != "user_confirmed"


def test_a_bad_value_does_not_lose_the_good_ones(pack, record) -> None:
    """The failure mode this guards against: one mistyped number rolls back
    four correct answers, and the user retypes all five."""
    payload = needs_payload()
    payload["business"]["user_count"] = "loads"

    result = apply_commit(record, pack, NEEDS, payload)

    assert not result.ok
    assert result.rejected[0][0] == "/uc/business/user_count"
    # The other three still landed.
    assert record.business.user_profile == "Claims handler"
    assert record.meta.initiative_name == "Claims triage"


def test_all_problems_are_reported_at_once(pack, record) -> None:
    """A user with two blanks and a bad number deserves to hear all three in
    one turn, not one per round trip."""
    payload = needs_payload()
    payload["business"]["user_profile"] = ""
    payload["business"]["problem_description"] = ""
    payload["business"]["user_count"] = "loads"

    result = apply_commit(record, pack, NEEDS, payload)

    assert len(result.missing) == 3  # the two blanks plus the rejected count
    assert len(result.rejected) == 1


def test_fixing_one_field_does_not_re_report_the_others(pack, record) -> None:
    """`missing_required` reads the record, not the payload, so a value
    confirmed on an earlier attempt still counts."""
    payload = needs_payload()
    payload["business"]["user_profile"] = ""
    apply_commit(record, pack, NEEDS, payload)

    # The user fills in only the field they were asked about.
    result = apply_commit(
        record, pack, NEEDS, {"business": {"user_profile": "Claims handler"}}
    )

    assert result.ok, result.blocking_summary()


def test_changes_record_before_and_after(pack, record) -> None:
    apply_commit(record, pack, NEEDS, needs_payload())
    result = apply_commit(
        record, pack, NEEDS, {"business": {"user_count": "20"}}
    )

    change = next(
        c for c in result.changes if c.path == "/uc/business/user_count"
    )
    assert change.before == 12
    assert change.after == 20
    assert change.changed


def test_multi_select_commits_as_a_list(pack, record) -> None:
    payload = {
        "technical": {
            "data_sources": ["sharepoint", "jira"],
            "security": {"data_classification": "internal"},
        }
    }

    result = apply_commit(record, pack, DATA, payload)

    assert record.technical.data_sources == ["sharepoint", "jira"]
    assert result.ok, result.blocking_summary()


def test_empty_multi_select_blocks_the_stage(pack, record) -> None:
    """"Not sure yet" is an option, so an empty list means the user skipped
    the question rather than answering it."""
    payload = {
        "technical": {
            "data_sources": [],
            "security": {"data_classification": "internal"},
        }
    }

    result = apply_commit(record, pack, DATA, payload)

    assert not result.ok
    assert [f.path for f in result.missing] == ["/uc/technical/data_sources"]


def test_not_sure_yet_is_an_answer(pack, record) -> None:
    """Choosing "Not sure yet" means the user considered the question. That
    is worth more than a blank and must not be conflated with one."""
    payload = {
        "technical": {
            "data_sources": ["unknown"],
            "security": {"data_classification": "unknown"},
        }
    }

    result = apply_commit(record, pack, DATA, payload)

    assert result.ok, result.blocking_summary()


# ---------------------------------------------------------------------------
# The write-once guard
# ---------------------------------------------------------------------------


def test_draft_writes_into_an_empty_field(record) -> None:
    assert draft(record, "/uc/business/user_profile", "Claims handler") is True
    assert record.business.user_profile == "Claims handler"
    assert record.provenance_of("business.user_profile") == "agent_draft"


def test_draft_refuses_to_overwrite_a_confirmed_value(pack, record) -> None:
    """The extractor re-reads the whole conversation each turn, so it will
    re-derive the value a user corrected two turns ago. Without this guard
    every manual correction has a lifetime of one turn."""
    apply_commit(record, pack, NEEDS, needs_payload())

    wrote = draft(record, "/uc/business/user_profile", "Underwriter")

    assert wrote is False
    assert record.business.user_profile == "Claims handler"


def test_draft_may_overwrite_an_earlier_draft(record) -> None:
    """Only human confirmation is sticky. A better guess replacing a worse
    one is the extractor working as intended."""
    draft(record, "/uc/business/user_profile", "Someone in ops")
    assert draft(record, "/uc/business/user_profile", "Claims handler") is True
    assert record.business.user_profile == "Claims handler"


def test_unconfirmed_in_stage_lists_drafts(pack, record) -> None:
    draft(record, "/uc/business/user_profile", "Claims handler")

    outstanding = unconfirmed_in_stage(record, pack.stages[NEEDS])

    assert [f.path for f in outstanding] == ["/uc/business/user_profile"]


def test_unconfirmed_in_stage_ignores_readonly(pack, record) -> None:
    """The `/ui/summary/*` fields are not record paths and would raise if
    resolved. This asserts the readonly filter short-circuits first."""
    assert unconfirmed_in_stage(record, pack.stages[SIZING]) == []


def test_confirmed_fields_are_not_outstanding(pack, record) -> None:
    apply_commit(record, pack, NEEDS, needs_payload())
    assert unconfirmed_in_stage(record, pack.stages[NEEDS]) == []


# ---------------------------------------------------------------------------
# Gate helper
# ---------------------------------------------------------------------------


def test_missing_required_on_an_untouched_record(pack, record) -> None:
    missing = missing_required(record, pack.stages[NEEDS])
    assert {f.path for f in missing} == {
        "/uc/meta/initiative_name",
        "/uc/business/problem_description",
        "/uc/business/user_profile",
        "/uc/business/user_count",
    }


def test_optional_fields_never_block(pack, record) -> None:
    apply_commit(record, pack, NEEDS, needs_payload())
    # department_bu and user_stories were never supplied.
    assert record.meta.department_bu is None
    assert missing_required(record, pack.stages[NEEDS]) == []
