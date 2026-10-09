"""Both packs write data_classification; both scorers must understand it.

Phase 1 (business.yaml) and Phase 2 (tech.yaml) once used different words for
the same field, so the most sensitive Phase 1 answer ("restricted") graded as
"Unclassified" in 3.4 and the tech pack's "regulated" never reached Level 6.
These tests walk every option value either pack can store through subcriterion
3.4 and the capability tier.
"""

from __future__ import annotations

import pytest

from qualify.packs.loader import load_all_packs
from qualify.schema.capability import CapabilityLevel
from qualify.schema.classification import VOCABULARY, normalise_classification
from qualify.schema.use_case_record import Business, Meta, Technical, UseCaseRecord
from qualify.scoring.business_tier import classify_capability
from qualify.scoring.technical import FAIL, PASS, WARN, score_technical
from tests.test_technical_scoring import perfect_record

#: value -> (3.4 points, 3.4 answered, Level 6?)
EXPECTED = {
    "public": (PASS, True, False),
    "internal": (PASS, True, False),
    "confidential": (PASS, True, False),
    "regulated": (PASS, True, True),
    "mixed": (WARN, True, False),
    "unclassified": (FAIL, True, False),
    "unknown": (FAIL, False, False),
    # Legacy Phase 1 value on records saved before the vocabularies merged.
    "restricted": (PASS, True, True),
}


def _pack_values() -> list[tuple[str, str]]:
    out = []
    for name, pack in load_all_packs().items():
        for stage in pack.stages:
            for field in stage.fields:
                if field.path.endswith("/security/data_classification"):
                    out += [(name, o.value) for o in pack.options_for(field)]
    return out


PACK_VALUES = _pack_values()


def test_both_packs_ask_for_classification() -> None:
    assert {name for name, _ in PACK_VALUES} == {"business", "tech"}


@pytest.mark.parametrize(("pack", "value"), PACK_VALUES)
def test_every_pack_option_is_in_the_shared_vocabulary(pack: str, value: str) -> None:
    assert value in VOCABULARY, f"{pack}.yaml stores {value!r}"


def _subscore_3_4(value: str):
    record = perfect_record()
    record.technical.security.data_classification = value
    return next(s for s in score_technical(record).subscores if s.id == "3.4")


def _level(value: str) -> CapabilityLevel:
    record = UseCaseRecord(
        meta=Meta(record_id="UC-2026-CLS01"),
        business=Business(problem_description="Summarise policies."),
        technical=Technical(data_sources=["google_drive"]),
    )
    record.technical.security.data_classification = value
    classify_capability(record)
    assert record.technical.capability_level is not None
    return record.technical.capability_level


@pytest.mark.parametrize(
    "value", sorted({v for _, v in PACK_VALUES} | {"restricted"})
)
def test_every_classification_is_graded_sensibly(value: str) -> None:
    points, answered, level6 = EXPECTED[value]

    s34 = _subscore_3_4(value)
    assert (s34.points, s34.answered) == (points, answered)
    assert "Unrecognised" not in s34.rationale

    level = _level(value)
    assert (level == CapabilityLevel.HIGH_CODE_AGENT) is level6


def test_restricted_is_not_scored_as_unclassified() -> None:
    assert "Unclassified" not in _subscore_3_4("restricted").rationale


def test_normalise_maps_legacy_values_and_keeps_canonical_ones() -> None:
    assert normalise_classification("restricted") == "regulated"
    assert normalise_classification(" Regulated ") == "regulated"
    assert normalise_classification("") is None
    assert normalise_classification(None) is None
    for value in VOCABULARY:
        assert normalise_classification(value) == value
