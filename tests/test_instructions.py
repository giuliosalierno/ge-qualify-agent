"""Tests that the agent instruction stays consistent with the code.

A prompt is not tested by running it, but parts of it are still checkable.
This file pins the facts the instruction asserts about the rest of the
system, because those are the parts that rot silently: someone adds a stage,
renames a capability level, and the prompt keeps confidently describing the
old shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.packs.loader import load_pack
from qualify.schema.capability import CapabilityLevel

INSTRUCTIONS = (
    Path(__file__).resolve().parent.parent / "agent" / "instructions.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8")


def test_the_instruction_exists() -> None:
    assert INSTRUCTIONS.is_file(), f"missing {INSTRUCTIONS}"


def test_every_capability_level_is_listed_verbatim(text: str) -> None:
    """The ladder appears in the prompt and in `CapabilityLevel`. If the two
    drift, the model will name a level the record cannot store."""
    for level in CapabilityLevel:
        assert level.label in text, f"missing label: {level.label}"
        assert f"| {level.value} |" in text, f"missing level number {level.value}"


def test_no_extra_levels_are_invented(text: str) -> None:
    """Guards the other direction: a level in the prompt that no longer
    exists in the enum."""
    for n in (0, 7, 8):
        assert f"| {n} |" not in text


def test_every_pack_stage_is_covered(text: str) -> None:
    """Four stages in the pack, four headings in the prompt. An uncovered
    stage means the agent walks into it with no questions prepared."""
    pack = load_pack("business")
    for stage in pack.stages:
        assert stage.label in text, f"stage not described: {stage.label}"


def test_stage_count_matches(text: str) -> None:
    pack = load_pack("business")
    assert f"## The {_word(len(pack.stages))} stages" in text


def _word(n: int) -> str:
    return {2: "two", 3: "three", 4: "four", 5: "five"}[n]


def test_arithmetic_is_forbidden(text: str) -> None:
    """`compute_derived()` owns the hours calculation. If the agent also does
    it, the two can disagree and the user cannot tell which to believe."""
    assert "Never do arithmetic in your head" in text


def test_layout_duties_are_disclaimed(text: str) -> None:
    """The compiler builds the form. An agent describing components will
    eventually describe one that is not there."""
    assert "Never describe the layout" in text


def test_zero_extrapolation_is_stated(text: str) -> None:
    assert "Do not infer, deduce, extrapolate or autocomplete" in text


def test_commit_semantics_are_explained(text: str) -> None:
    """`commit_stage` is the only inbound path, so the prompt has to make
    Continue meaningful to the user."""
    assert "Continue is the commit" in text


def test_coe_owned_outputs_are_off_limits(text: str) -> None:
    """Scores, priority and dates belong to the CoE. The schema refuses the
    writes; the prompt should stop the agent claiming them in prose, which
    the schema cannot catch."""
    assert "Do not score it yourself" in text


def test_ownership_fields_are_framed_as_proposals(text: str) -> None:
    assert "proposals, not decisions" in text.lower()
