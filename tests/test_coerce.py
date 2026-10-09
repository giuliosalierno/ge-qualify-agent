"""Tests for turning widget strings into record values.

The cases worth writing here are the ones where a plausible implementation
does the wrong thing quietly. `int("")` raising is not interesting; `""`
becoming `0` is, because nothing downstream can tell that apart from a real
measurement of zero.
"""

from __future__ import annotations

from datetime import date

import pytest

from qualify.schema.capability import CapabilityLevel
from qualify.schema.coerce import (
    CoercionError,
    coerce_and_set,
    coerce_value,
    get_by_path,
    set_by_path,
)
from qualify.schema.paths import resolve_record_path
from qualify.schema.use_case_record import Meta, OwnershipError, UseCaseRecord

# Real paths, confirmed against the frozen schema.
COUNT = "/uc/business/user_count"  # int, ge=0
RATE = "/uc/sizing/task_frequency_weekly"  # float, ge=0
MINUTES = "/uc/sizing/target_minutes_saved_per_task"  # float, ge=0
LEVEL = "/uc/technical/capability_level"  # CapabilityLevel
SOURCES = "/uc/technical/data_sources"  # list[str]
ACL = "/uc/technical/grounding/acl_preservation_required"  # bool
SUBMITTED = "/uc/meta/submission_date"  # date
COE_SCORE = "/uc/scoring/business_value_score"  # CoE owned


@pytest.fixture
def record() -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="uc-coerce"))


def coerce(path: str, raw: object) -> object:
    return coerce_value(resolve_record_path(path), raw)


# ---------------------------------------------------------------------------
# Blank is not zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_number_is_none_not_zero(blank: object) -> None:
    """The distinction the whole module exists to protect.

    `0` minutes saved is a finding. A blank field is an unanswered question.
    Collapsing them would let the agent report a measurement nobody made.
    """
    assert coerce(MINUTES, blank) is None


def test_zero_survives_as_zero() -> None:
    """The flip side: a real zero must not be mistaken for a blank."""
    assert coerce(MINUTES, "0") == 0.0


def test_blank_list_is_empty_list_not_none() -> None:
    """A list field's blank is `[]`, so callers can iterate without a guard."""
    assert coerce(SOURCES, "") == []


def test_empty_list_does_not_crash() -> None:
    """Regression: `[] in {"", None}` raised TypeError, because a set hashes
    its argument. Every multi-select submitted empty would have killed the
    turn."""
    assert coerce(SOURCES, []) == []


def test_list_of_values_passes_through() -> None:
    assert coerce(SOURCES, ["sharepoint", "confluence"]) == [
        "sharepoint",
        "confluence",
    ]


def test_list_drops_blank_entries() -> None:
    assert coerce(SOURCES, ["sharepoint", "", "  "]) == ["sharepoint"]


def test_lone_string_becomes_a_one_item_list() -> None:
    """Tolerated in case a renderer collapses a single-item array. Losing the
    user's one selection over that would be a poor trade."""
    assert coerce(SOURCES, "sharepoint") == ["sharepoint"]


def test_list_rejects_a_non_list_non_string() -> None:
    with pytest.raises(CoercionError, match="expected a list"):
        coerce(SOURCES, 42)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def test_thousands_separator_is_stripped() -> None:
    """Users type "1,200". Rejecting it makes them retype a correct number."""
    assert coerce(COUNT, "1,200") == 1200


def test_fraction_rejected_on_an_int_field() -> None:
    """You cannot have 10.5 users. Rounding silently would invent precision."""
    with pytest.raises(CoercionError, match="whole number"):
        coerce(COUNT, "10.5")


def test_float_field_keeps_its_fraction() -> None:
    assert coerce(RATE, "2.5") == 2.5


def test_gibberish_number_is_rejected() -> None:
    with pytest.raises(CoercionError, match="not a number"):
        coerce(COUNT, "quite a lot")


def test_bool_is_not_a_number() -> None:
    """`float(True)` is 1.0 in Python, so a checkbox value would slide into a
    count field unnoticed. That type confusion is worth refusing."""
    with pytest.raises(CoercionError, match="boolean"):
        coerce(COUNT, True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_enum_from_member_number() -> None:
    """What a Material single-select actually sends, per the L12 probe."""
    assert coerce(LEVEL, "6") is CapabilityLevel.HIGH_CODE_AGENT


def test_enum_from_member_name() -> None:
    assert coerce(LEVEL, "HIGH_CODE_AGENT") is CapabilityLevel.HIGH_CODE_AGENT


def test_enum_passthrough() -> None:
    assert (
        coerce(LEVEL, CapabilityLevel.CUSTOM_SKILL)
        is CapabilityLevel.CUSTOM_SKILL
    )


def test_enum_rejects_an_unknown_value() -> None:
    with pytest.raises(CoercionError) as exc:
        coerce(LEVEL, "99")
    # The message must name the valid options, or the agent cannot coach the
    # user out of the error.
    assert "HIGH_CODE_AGENT" in str(exc.value)


def test_blank_enum_is_none() -> None:
    assert coerce(LEVEL, "") is None


# ---------------------------------------------------------------------------
# Booleans and dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["true", "TRUE", "yes", "1", "on", True])
def test_truthy_values(raw: object) -> None:
    assert coerce(ACL, raw) is True


@pytest.mark.parametrize("raw", ["false", "no", "0", "off", False])
def test_falsy_values(raw: object) -> None:
    assert coerce(ACL, raw) is False


def test_maybe_is_not_a_boolean() -> None:
    """Guessing here would record an access-control answer nobody gave."""
    with pytest.raises(CoercionError, match="yes/no"):
        coerce(ACL, "maybe")


def test_iso_date() -> None:
    assert coerce(SUBMITTED, "2026-01-15") == date(2026, 1, 15)


def test_iso_datetime_with_z_suffix() -> None:
    """`DateTimeInput` sends a full timestamp, and Python's parser rejects a
    bare `Z` suffix, so it has to be rewritten before parsing."""
    assert coerce(SUBMITTED, "2026-01-15T09:30:00Z") == date(2026, 1, 15)


def test_bad_date_is_rejected() -> None:
    with pytest.raises(CoercionError, match="ISO date"):
        coerce(SUBMITTED, "next tuesday")


# ---------------------------------------------------------------------------
# Writing into the record
# ---------------------------------------------------------------------------


def test_coerce_and_set_round_trips(record: UseCaseRecord) -> None:
    coerce_and_set(record, COUNT, "42")
    assert record.business.user_count == 42
    assert get_by_path(record, COUNT) == 42


def test_writes_through_a_nested_container(record: UseCaseRecord) -> None:
    """Two levels down, to prove the walk does not stop at the first model."""
    coerce_and_set(record, ACL, "yes")
    assert record.technical.grounding.acl_preservation_required is True


def test_dotted_paths_work_too(record: UseCaseRecord) -> None:
    coerce_and_set(record, "uc.business.user_count", "7")
    assert record.business.user_count == 7


def test_ownership_is_enforced_at_the_write(record: UseCaseRecord) -> None:
    """The CoE owns scoring. The agent must not write it, however it is asked.

    This is the chokepoint the ownership rule depends on, so it is tested
    here and not left to the schema-level check alone.
    """
    with pytest.raises(OwnershipError):
        set_by_path(record, COE_SCORE, 5)


def test_pydantic_validation_still_fires_on_assignment(
    record: UseCaseRecord,
) -> None:
    """`validate_assignment=True` means a negative head count fails at the
    write, not hours later at serialisation — and fails as a CoercionError,
    which is the only kind `apply_commit` turns into a per-field rejection."""
    with pytest.raises(CoercionError):
        coerce_and_set(record, COUNT, "-5")
    assert record.business.user_count is None


@pytest.mark.parametrize("path", [COUNT, RATE, MINUTES])
@pytest.mark.parametrize(
    "raw", ["-5", -5, "1e400", "inf", "-inf", "nan", "NaN", float("inf"), 10**400]
)
def test_out_of_range_and_non_finite_numbers_raise_coercion_error(
    record: UseCaseRecord, path: str, raw: object
) -> None:
    """ValidationError, OverflowError and plain ValueError used to escape."""
    with pytest.raises(CoercionError):
        coerce_and_set(record, path, raw)
    assert get_by_path(record, path) is None


def test_non_finite_float_is_rejected_by_the_schema_too() -> None:
    """Belt and braces: a direct assignment cannot store infinity either."""
    from pydantic import ValidationError

    r = UseCaseRecord(meta=Meta(record_id="uc-inf"))
    with pytest.raises(ValidationError):
        r.sizing.task_frequency_weekly = float("inf")


def test_blank_clears_rather_than_zeroes(record: UseCaseRecord) -> None:
    """A user who empties a field they previously filled should end up with
    an unanswered field, not a zero."""
    coerce_and_set(record, COUNT, "10")
    coerce_and_set(record, COUNT, "")
    assert record.business.user_count is None
