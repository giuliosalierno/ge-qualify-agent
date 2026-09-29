"""Tests for the Systems & Data Landscape extractor.

This is the least defensible part of the technical review: an LLM building a
structured inventory from prose. The tests are correspondingly focused on what
it must *refuse* to do, because an invented hosting location reads exactly like
a discovered one and would flow straight into the dossier and the score.
"""

from __future__ import annotations

from typing import Any

import pytest

from qualify.a2ui.systems_extractor import (
    extract_systems,
    merge_systems,
    render_systems_table,
)
from qualify.schema.use_case_record import Meta, SystemEntry, UseCaseRecord


class StubClient:
    """Returns a canned response, or raises if `error` is set."""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def propose(self, *, instruction: str, schema: dict, conversation: str) -> Any:
        self.calls.append({"instruction": instruction, "conversation": conversation})
        if self.error:
            raise self.error
        return self.response


CONVO = (
    "Reviewer: Which backends hold this data?\n"
    "User: It's mostly SAP ECC, which runs in our Frankfurt data centre, "
    "and Salesforce for the customer side. Anna Rossi owns the SAP instance."
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_extracts_systems_with_the_attributes_that_were_stated() -> None:
    client = StubClient([
        {
            "name": "SAP ECC",
            "hosting_location": "Frankfurt data centre",
            "system_owner": "Anna Rossi",
            "function": "",
            "interface": "",
            "data_format": "",
            "schema_status": "",
            "evidence": "SAP ECC, which runs in our Frankfurt data centre",
        },
        {
            "name": "Salesforce",
            "hosting_location": "",
            "evidence": "Salesforce for the customer side",
        },
    ])

    systems = extract_systems(CONVO, client)

    assert [s.name for s in systems] == ["SAP ECC", "Salesforce"]
    assert systems[0].hosting_location == "Frankfurt data centre"
    assert systems[0].system_owner == "Anna Rossi"
    # Unstated attributes are None, not empty strings — one way to say "unknown".
    assert systems[0].interface is None
    assert systems[1].hosting_location is None


def test_empty_response_is_a_valid_answer() -> None:
    assert extract_systems(CONVO, StubClient([])) == []


# ---------------------------------------------------------------------------
# What it must refuse
# ---------------------------------------------------------------------------


def test_discards_a_system_whose_evidence_is_not_in_the_transcript() -> None:
    """The guard against invented systems.

    A model that adds "Oracle" because SAP deployments usually have one will
    not be able to quote the user saying it.
    """
    client = StubClient([
        {"name": "Oracle DB", "evidence": "we also use Oracle for the ledger"},
        {"name": "SAP ECC", "evidence": "It's mostly SAP ECC"},
    ])

    systems = extract_systems(CONVO, client)

    assert [s.name for s in systems] == ["SAP ECC"]


def test_discards_entries_with_no_evidence_at_all() -> None:
    client = StubClient([{"name": "SAP ECC", "evidence": ""}])
    assert extract_systems(CONVO, client) == []


def test_discards_entries_with_no_name() -> None:
    client = StubClient([{"name": "   ", "evidence": "It's mostly SAP ECC"}])
    assert extract_systems(CONVO, client) == []


@pytest.mark.parametrize("junk", [None, "a string", {"not": "a list"}, 42])
def test_a_malformed_response_costs_the_table_not_the_turn(junk: Any) -> None:
    assert extract_systems(CONVO, StubClient(junk)) == []


def test_a_failed_call_preserves_what_was_already_found() -> None:
    """An outage must not erase the matrix built over previous turns."""
    existing = [SystemEntry(name="SAP ECC", hosting_location="Frankfurt")]
    client = StubClient(error=RuntimeError("503 from Vertex"))

    systems = extract_systems(CONVO, client, existing)

    assert [s.name for s in systems] == ["SAP ECC"]
    assert systems[0].hosting_location == "Frankfurt"


# ---------------------------------------------------------------------------
# Merging across turns
# ---------------------------------------------------------------------------


def test_a_later_turn_fills_gaps_without_blanking_known_facts() -> None:
    """The failure this merge exists to prevent.

    The extractor sees one excerpt at a time and returns empty strings for
    everything that excerpt did not mention. Treating those as deletions would
    lose a fact every time the reviewer changed subject.
    """
    existing = [
        SystemEntry(
            name="SAP ECC", hosting_location="Frankfurt", interface="RFC/BAPI"
        )
    ]
    incoming = [SystemEntry(name="SAP ECC", system_owner="Anna Rossi")]

    merged = merge_systems(existing, incoming)

    assert len(merged) == 1
    assert merged[0].hosting_location == "Frankfurt"
    assert merged[0].interface == "RFC/BAPI"
    assert merged[0].system_owner == "Anna Rossi"


def test_a_later_turn_can_correct_a_stated_attribute() -> None:
    existing = [SystemEntry(name="SAP ECC", hosting_location="Frankfurt")]
    incoming = [SystemEntry(name="SAP ECC", hosting_location="Dublin")]

    assert merge_systems(existing, incoming)[0].hosting_location == "Dublin"


def test_merging_matches_names_case_insensitively() -> None:
    existing = [SystemEntry(name="SAP ECC", hosting_location="Frankfurt")]
    incoming = [SystemEntry(name="sap ecc", system_owner="Anna Rossi")]

    merged = merge_systems(existing, incoming)

    assert len(merged) == 1
    # The first spelling wins, so the matrix does not flip between turns.
    assert merged[0].name == "SAP ECC"


def test_a_genuinely_new_system_is_appended() -> None:
    existing = [SystemEntry(name="SAP ECC")]
    incoming = [SystemEntry(name="Salesforce")]

    assert [s.name for s in merge_systems(existing, incoming)] == [
        "SAP ECC",
        "Salesforce",
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _record(**tech: Any) -> UseCaseRecord:
    r = UseCaseRecord(meta=Meta(record_id="UC-2026-R"))
    for k, v in tech.items():
        setattr(r.technical, k, v)
    return r


def test_table_marks_every_missing_attribute() -> None:
    record = _record(systems=[SystemEntry(name="SAP ECC", hosting_location="Frankfurt")])
    table = render_systems_table(record)

    assert "| SAP ECC | Frankfurt |" in table
    assert "⚠️ marks 3 attribute(s) still to confirm." in table


def test_a_complete_row_has_no_warnings() -> None:
    record = _record(
        systems=[
            SystemEntry(
                name="SAP ECC",
                hosting_location="Frankfurt",
                interface="REST",
                schema_status="Documented",
                system_owner="Anna Rossi",
            )
        ]
    )
    assert "⚠️" not in render_systems_table(record)


def test_falls_back_to_the_phase_1_inventory() -> None:
    """Before the review itemises anything, Phase 1's list is all there is."""
    record = _record(data_sources=["sharepoint", "salesforce"])
    table = render_systems_table(record)

    assert "sharepoint, salesforce" in table
    assert "None verified technically yet" in table


def test_says_so_plainly_when_nothing_is_known() -> None:
    table = render_systems_table(_record())
    assert "No systems recorded yet" in table
