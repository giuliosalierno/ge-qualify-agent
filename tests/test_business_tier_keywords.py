"""Keyword signals in the capability tier match whole words, not substrings.

A substring test read "committee" as "commit", "written" as "write", "async"
as "sync", "routine" as "route", "steps" as "step" and "loophole" as "loop".
With two named sources that pushed harmless, read-only descriptions to
Level 6 High-Code.
"""

from __future__ import annotations

import pytest

from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import Business, Meta, Technical, UseCaseRecord
from qualify.scoring.business_tier import (
    _COMPLEX_ORCHESTRATION_RE,
    _MUTATION_RE,
    _WORKFLOW_RE,
    classify_capability,
)


def _classify(problem: str, sources: list[str]) -> CapabilityLevel | None:
    record = UseCaseRecord(
        meta=Meta(record_id="UC-2026-KW01"),
        business=Business(problem_description=problem),
        technical=Technical(data_sources=sources),
    )
    classify_capability(record)
    return record.technical.capability_level


@pytest.mark.parametrize(
    "problem",
    [
        (
            "Our steering committee reads written status reports and asks "
            "routine questions about them."
        ),
        "Teams work across time zones and async handoffs slow us down.",
        "Analysts look for loopholes in the policy documents.",
    ],
)
def test_harmless_read_only_descriptions_stay_low_code(problem: str) -> None:
    level = _classify(problem, ["google_drive", "confluence"])
    assert level == CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT


@pytest.mark.parametrize(
    "word", ["committee", "commitment", "written", "async", "asynchronous"]
)
def test_mutation_false_positives(word: str) -> None:
    assert not _MUTATION_RE.search(f"the {word} is here")


@pytest.mark.parametrize("word", ["routine", "steps", "assignment", "rerouted"])
def test_workflow_false_positives(word: str) -> None:
    assert not _WORKFLOW_RE.search(f"the {word} is here")


@pytest.mark.parametrize("word", ["loophole", "loopback"])
def test_orchestration_false_positives(word: str) -> None:
    assert not _COMPLEX_ORCHESTRATION_RE.search(f"the {word} is here")


@pytest.mark.parametrize(
    "text",
    [
        "commit the change",
        "it commits nightly",
        "changes are committed to SAP",
        "write back to the CRM",
        "the bot writes to Jira",
        "writing to the ledger",
        "sync the two systems",
        "data is synced hourly",
        "update records in Salesforce",
        "Delete stale entries",
        "post to the channel",
    ],
)
def test_mutation_verbs_still_detected(text: str) -> None:
    assert _MUTATION_RE.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "route tickets to the right team",
        "tickets are routed by region",
        "a step-by-step checklist",
        "automate the handoff",
        "send  email to the owner",
        "notify the approver",
    ],
)
def test_workflow_verbs_still_detected(text: str) -> None:
    assert _WORKFLOW_RE.search(text)


@pytest.mark.parametrize("text", ["retry in a loop", "loops until done", "two-way sync"])
def test_orchestration_markers_still_detected(text: str) -> None:
    assert _COMPLEX_ORCHESTRATION_RE.search(text)


def test_real_cross_system_write_still_escalates_to_level_6() -> None:
    level = _classify(
        "Agents write approved changes back to both systems.",
        ["google_drive", "confluence"],
    )
    assert level == CapabilityLevel.HIGH_CODE_AGENT
