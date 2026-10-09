"""Typed commands must be whole short messages, never words inside an answer.

Every command check runs before the interview sees the message, so a loose
substring match swallows the user's intake answer: "we need search
capabilities over our contracts" used to return the help menu, and "today
every change needs an architecture review" opened the review queue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.agent import turn as turn_mod
from qualify.agent.commands import match_command
from qualify.agent.handover import (
    looks_like_pending_review_pick,
    parse_tech_review_intent,
    resolve_pending_review_choice,
)
from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors.sharepoint import SharePointConnector
from qualify.schema.use_case_record import Business, Meta, UseCaseRecord
from qualify.sinks.session import InMemorySessionStore

INTAKE_ANSWERS = [
    "we need search capabilities over our contracts",
    "agents run shell commands",
    "today every change needs an architecture review",
    "we rank use cases by cost today",
    "legal has 30 pending reviews a week",
]


@pytest.mark.parametrize("text", INTAKE_ANSWERS)
def test_intake_answers_are_not_commands(text: str) -> None:
    assert match_command(text, turn_mod._HELP_PHRASES, max_tail_words=2) is None
    assert match_command(text, turn_mod._PORTFOLIO_TRIGGERS, max_tail_words=3) is None
    assert parse_tech_review_intent(text) == (False, None)


@pytest.mark.parametrize(
    "text",
    ["what can you do?", "Capabilities", "show commands", "commands", "How does this work?"],
)
def test_help_phrases_still_work(text: str) -> None:
    assert match_command(text, turn_mod._HELP_PHRASES, max_tail_words=2)


@pytest.mark.parametrize(
    "text",
    ["portfolio review", "Run the portfolio review please", "rank use cases", "CoE review"],
)
def test_portfolio_triggers_still_work(text: str) -> None:
    assert match_command(text, turn_mod._PORTFOLIO_TRIGGERS, max_tail_words=3)


@pytest.mark.parametrize(
    "text",
    [
        "show pending documents to review",
        "I want to start a technical review",
        "architecture review for AP Invoice Exception Assistant",
        "list pending",
    ],
)
def test_tech_review_triggers_still_work(text: str) -> None:
    assert parse_tech_review_intent(text)[0] is True


def _start_intake(store: InMemorySessionStore, context_id: str) -> None:
    out = execute_turn(store, TurnInput(context_id=context_id, user_text="hello"))
    assert out.session.pack_name == "business"
    assert out.session.rendered_stages


@pytest.mark.parametrize("text", INTAKE_ANSWERS)
def test_intake_answer_reaches_the_interview(text: str) -> None:
    store = InMemorySessionStore(quiet=True)
    _start_intake(store, "ctx-intake")
    out = execute_turn(store, TurnInput(context_id="ctx-intake", user_text=text))

    # The interview answered, on the Stage 1 topic, not some command.
    assert "Problem and users" in out.reply_text
    assert out.reply_text != turn_mod._welcome_banner()
    assert "Welcome" not in out.reply_text
    assert "Portfolio" not in out.reply_text
    assert "Technical Architecture Dossier yet" not in out.reply_text
    assert out.session.pack_name == "business"
    assert not out.session.pending_review_choices


# ---------------------------------------------------------------------------
# Pending-review picker
# ---------------------------------------------------------------------------

_CHOICES = [
    {"recordId": "UC-2026-AAAAAA", "initiativeName": "Contracts"},
    {"recordId": "UC-2026-BBBBBB", "initiativeName": "AP Invoice Exception Assistant"},
]


def test_sentence_mentioning_a_name_is_not_a_pick() -> None:
    assert (
        resolve_pending_review_choice(
            "we need search capabilities over our contracts", _CHOICES
        )
        is None
    )
    assert resolve_pending_review_choice("agents run shell commands", _CHOICES) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("contracts", "UC-2026-AAAAAA"),
        ("let's start with Contracts", "UC-2026-AAAAAA"),
        ("the contracts one please", "UC-2026-AAAAAA"),
        ("invoice exception", "UC-2026-BBBBBB"),
        ("2", "UC-2026-BBBBBB"),
    ],
)
def test_real_picks_still_resolve(text: str, expected: str) -> None:
    assert resolve_pending_review_choice(text, _CHOICES) == expected


def test_pick_attempt_detection() -> None:
    assert looks_like_pending_review_pick("option 7")
    assert looks_like_pending_review_pick("let's start with ZZZ")
    assert not looks_like_pending_review_pick("we rank use cases by cost today")


def test_portfolio_view_does_not_trap_the_next_intake_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the portfolio mid-intake arms the picker; the next ordinary
    answer must clear it and continue the interview, not demand a pick."""
    mock_sp = SharePointConnector(mock_dir=tmp_path / "sp_mock")
    monkeypatch.setattr(
        "qualify.connectors.sharepoint.get_sharepoint_connector", lambda: mock_sp
    )
    mock_sp.sync_opportunity(
        UseCaseRecord(
            meta=Meta(record_id="UC-2026-0CD0BC", initiative_name="Contracts"),
            business=Business(problem_description="Manual contract triage."),
        ),
        pack_name="business",
    )
    store = InMemorySessionStore(quiet=True)
    _start_intake(store, "ctx-trap")

    out = execute_turn(
        store, TurnInput(context_id="ctx-trap", user_text="portfolio review")
    )
    assert out.session.pending_review_choices

    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-trap",
            user_text="we need search capabilities over our contracts",
        ),
    )
    assert "couldn't match" not in out.reply_text
    assert out.session.pack_name == "business"
    assert not out.session.pending_review_choices
    assert not store.load("ctx-trap").pending_review_choices
