"""Tests for the field extractor.

The extractor is the one component allowed to be wrong, so almost every test
here is about what it refuses. The guiding trade: an empty field costs one
more question, a confidently wrong field costs trust in every other number on
the brief.

A stub client stands in for the model, so these run with no SDK, no
credentials and no network.
"""

from __future__ import annotations

from typing import Any

import pytest

from qualify.a2ui.patcher import (
    ExtractionClient,
    FieldDraft,
    apply_drafts,
    build_field_menu,
    build_instruction,
    build_schema,
    extract_drafts,
)
from qualify.a2ui.provenance import apply_commit
from qualify.packs.loader import load_pack
from qualify.schema.use_case_record import Meta, UseCaseRecord

NEEDS = 0
SIZING = 1
DATA = 2

CONVERSATION = """\
Assistant: What is the problem, and who does this work today?
User: Our claims handlers re-key every claim by hand from email into the \
policy system. There are about 12 of them and it takes roughly 20 minutes \
each time. We call it Claims Triage.
"""


class StubClient:
    """Returns whatever the test told it to, and records what it was asked."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.instruction: str | None = None
        self.schema: dict | None = None
        self.conversation: str | None = None

    def propose(
        self, *, instruction: str, schema: dict, conversation: str
    ) -> Any:
        self.instruction = instruction
        self.schema = schema
        self.conversation = conversation
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ExplodingClient:
    def propose(self, **_: Any) -> Any:
        raise RuntimeError("model unavailable")


@pytest.fixture
def pack():
    return load_pack("business")


@pytest.fixture
def record() -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="uc-patch"))


def run(pack, response, stage_idx=NEEDS, conversation=CONVERSATION):
    return extract_drafts(
        pack.stages[stage_idx], pack, conversation, StubClient(response)
    )


def test_stub_satisfies_the_protocol() -> None:
    """If the stub drifts from the Protocol these tests stop meaning anything."""
    client: ExtractionClient = StubClient([])
    assert client is not None


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_accepts_a_well_evidenced_value(pack) -> None:
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "12",
                "evidence": "about 12 of them",
            }
        ],
    )

    assert result.paths() == ["/uc/business/user_count"]
    assert result.drafts[0].value == 12  # coerced, not left a string


def test_accepts_several_fields_at_once(pack) -> None:
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "12",
                "evidence": "about 12 of them",
            },
            {
                "path": "/uc/business/user_profile",
                "value": "Claims handler",
                "evidence": "Our claims handlers",
            },
        ],
    )

    assert len(result.drafts) == 2


def test_evidence_matching_ignores_case_and_spacing(pack) -> None:
    """Quoting should not be a typing test. The guard is about invention."""
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "12",
                "evidence": "About   12 OF THEM.",
            }
        ],
    )

    assert len(result.drafts) == 1


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_invented_evidence_is_refused(pack) -> None:
    """The central guard. A model inventing a value must also invent a quote,
    and an invented quote does not survive a check against the transcript."""
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "40",
                "evidence": "we have around forty staff on the team",
            }
        ],
    )

    assert result.drafts == []
    assert "evidence not found" in result.rejected[0][1]


def test_missing_evidence_is_refused(pack) -> None:
    result = run(
        pack,
        [{"path": "/uc/business/user_count", "value": "12", "evidence": ""}],
    )

    assert result.drafts == []
    assert result.rejected[0][1] == "no evidence quoted"


def test_a_field_from_another_stage_is_refused(pack) -> None:
    """Confirmation should mean "the user just saw this". Filling stage 2's
    fields while stage 1 is on screen breaks that."""
    result = run(
        pack,
        [
            {
                "path": "/uc/sizing/baseline_minutes_per_task",
                "value": "20",
                "evidence": "takes roughly 20 minutes",
            }
        ],
        stage_idx=NEEDS,
    )

    assert result.drafts == []
    assert "not a writable field of this stage" in result.rejected[0][1]


def test_the_same_field_in_the_right_stage_is_accepted(pack) -> None:
    """Confirms the previous test refused it for the stage, not the value."""
    result = run(
        pack,
        [
            {
                "path": "/uc/sizing/baseline_minutes_per_task",
                "value": "20",
                "evidence": "takes roughly 20 minutes",
            }
        ],
        stage_idx=SIZING,
    )

    assert len(result.drafts) == 1


def test_a_hallucinated_path_is_refused(pack) -> None:
    result = run(
        pack,
        [
            {
                "path": "/uc/business/vibe",
                "value": "good",
                "evidence": "Our claims handlers",
            }
        ],
    )

    assert result.drafts == []


def test_a_readonly_field_is_refused(pack) -> None:
    """The `/ui/summary/*` lines are compiler output. Nothing writes them."""
    result = run(
        pack,
        [
            {
                "path": "/ui/summary/hours_line",
                "value": "lots",
                "evidence": "takes roughly 20 minutes",
            }
        ],
        stage_idx=SIZING,
    )

    assert result.drafts == []


def test_an_uncoercible_value_is_refused(pack) -> None:
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "a dozen",
                "evidence": "about 12 of them",
            }
        ],
    )

    assert result.drafts == []
    assert "not a number" in result.rejected[0][1]


def test_an_invented_option_is_refused(pack) -> None:
    """Closed vocabularies are closed. A near-miss is still a miss."""
    convo = "User: we keep everything in Sharepoint and our wiki"
    result = run(
        pack,
        [
            {
                "path": "/uc/technical/data_sources",
                "value": ["sharepoint", "our_wiki"],
                "evidence": "Sharepoint and our wiki",
            }
        ],
        stage_idx=DATA,
        conversation=convo,
    )

    assert result.drafts == []
    assert "not one of the declared options" in result.rejected[0][1]


def test_valid_options_are_accepted(pack) -> None:
    convo = "User: we keep everything in Sharepoint and Jira"
    result = run(
        pack,
        [
            {
                "path": "/uc/technical/data_sources",
                "value": ["sharepoint", "jira"],
                "evidence": "Sharepoint and Jira",
            }
        ],
        stage_idx=DATA,
        conversation=convo,
    )

    assert result.drafts[0].value == ["sharepoint", "jira"]


def test_duplicates_keep_the_first(pack) -> None:
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "12",
                "evidence": "about 12 of them",
            },
            {
                "path": "/uc/business/user_count",
                "value": "13",
                "evidence": "about 12 of them",
            },
        ],
    )

    assert len(result.drafts) == 1
    assert result.drafts[0].value == 12


def test_a_blank_value_is_refused(pack) -> None:
    """The model found the field but not a value in it. Nothing to record."""
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_profile",
                "value": "",
                "evidence": "Our claims handlers",
            }
        ],
    )

    assert result.drafts == []


def test_one_bad_draft_does_not_discard_the_good_ones(pack) -> None:
    result = run(
        pack,
        [
            {
                "path": "/uc/business/user_count",
                "value": "99",
                "evidence": "ninety nine people",  # not in the transcript
            },
            {
                "path": "/uc/business/user_profile",
                "value": "Claims handler",
                "evidence": "Our claims handlers",
            },
        ],
    )

    assert result.paths() == ["/uc/business/user_profile"]
    assert len(result.rejected) == 1


# ---------------------------------------------------------------------------
# Malformed responses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response", [None, {}, "a string", [None], [["nested"]], [{"value": "12"}]]
)
def test_malformed_responses_never_raise(pack, response) -> None:
    """A confused model should cost the turn its auto-fill, not the
    conversation."""
    result = run(pack, response)
    assert result.drafts == []


def test_a_failed_call_never_raises(pack) -> None:
    result = extract_drafts(
        pack.stages[NEEDS], pack, CONVERSATION, ExplodingClient()
    )

    assert result.drafts == []
    assert "model unavailable" in result.rejected[0][1]


def test_an_empty_list_is_a_normal_answer(pack) -> None:
    result = run(pack, [])
    assert result.drafts == []
    assert result.rejected == []


# ---------------------------------------------------------------------------
# Prompt and schema
# ---------------------------------------------------------------------------


def test_schema_pins_paths_to_this_stage(pack) -> None:
    schema = build_schema(pack.stages[NEEDS], pack)
    paths = schema["items"]["properties"]["path"]["enum"]

    assert "/uc/business/user_count" in paths
    assert "/uc/sizing/task_frequency_weekly" not in paths


def test_schema_omits_readonly_fields(pack) -> None:
    schema = build_schema(pack.stages[SIZING], pack)
    paths = schema["items"]["properties"]["path"]["enum"]

    assert not any(p.startswith("/ui/") for p in paths)


def test_schema_requires_evidence(pack) -> None:
    schema = build_schema(pack.stages[NEEDS], pack)
    assert "evidence" in schema["items"]["required"]


def test_menu_lists_option_values_not_labels(pack) -> None:
    """The model must emit `sharepoint`, not `SharePoint`."""
    menu = build_field_menu(pack.stages[DATA], pack)

    assert "sharepoint" in menu
    assert "confidential" in menu


def test_menu_explains_each_option_value(pack) -> None:
    """Bare codes hid that "the shared Outlook mailbox" is `workspace_mail`."""
    menu = build_field_menu(pack.stages[DATA], pack)

    assert "workspace_mail = Email or calendar" in menu
    assert "Outlook" in menu


def test_instruction_licenses_returning_nothing(pack) -> None:
    """The bias to under-fill has to be stated, not implied."""
    instruction = build_instruction(pack.stages[NEEDS], pack)
    assert "empty list is a correct answer" in instruction


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def test_apply_writes_and_marks_as_draft(pack, record) -> None:
    drafts = [FieldDraft("/uc/business/user_count", 12, "about 12 of them")]

    applied = apply_drafts(record, drafts)

    assert applied == drafts
    assert record.business.user_count == 12
    assert record.provenance_of("business.user_count") == "agent_draft"


def test_apply_never_overwrites_a_confirmed_field(pack, record) -> None:
    """The extractor re-reads the whole conversation every turn, so without
    this a manual correction would survive exactly one turn."""
    apply_commit(
        record,
        pack,
        NEEDS,
        {
            "meta": {"initiative_name": "Claims triage"},
            "business": {
                "problem_description": "Re-keying by hand.",
                "user_profile": "Claims handler",
                "user_count": "12",
            },
        },
    )

    applied = apply_drafts(
        record, [FieldDraft("/uc/business/user_count", 99, "about 12 of them")]
    )

    assert applied == []
    assert record.business.user_count == 12
