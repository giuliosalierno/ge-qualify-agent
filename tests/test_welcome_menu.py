"""Tests for the welcome menu: a greeting opens three workflow buttons."""

from __future__ import annotations

import pytest

from qualify.a2ui.validate import validate_surface
from qualify.a2ui.views.events import OPEN_PORTFOLIO, START_INTAKE, START_TECH_REVIEW
from qualify.a2ui.views.welcome import build_welcome_menu
from qualify.agent.turn import TurnInput, _is_greeting, execute_turn
from qualify.sinks.session import InMemorySessionStore


@pytest.fixture(autouse=True)
def _menu_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overrides the suite-wide default in conftest.py."""
    monkeypatch.setenv("WELCOME_MENU", "1")


def _events(messages: list[dict]) -> set[str]:
    names: set[str] = set()
    for msg in messages:
        for comp in msg.get("updateComponents", {}).get("components", []):
            event = comp.get("action", {}).get("event")
            if event:
                names.add(event["name"])
    return names


def _has_stage_form(messages: list[dict]) -> bool:
    return any(
        "qualify-s0" in msg.get("createSurface", {}).get("surfaceId", "")
        for msg in messages
    )


@pytest.mark.parametrize(
    "text", ["hello", "Hi!", "hey there", "Good morning", "ciao 👋", "let's start"]
)
def test_greetings_are_recognised(text: str) -> None:
    assert _is_greeting(text)


@pytest.mark.parametrize(
    "text",
    [
        "hi, our claims team re-keys every invoice into SAP by hand",
        "Invoice triage assistant",
        "technical review",
        "",
    ],
)
def test_use_case_content_is_not_a_greeting(text: str) -> None:
    assert not _is_greeting(text)


def test_menu_card_offers_three_workflows() -> None:
    messages = build_welcome_menu("qualify-welcome-1")
    assert _events(messages) == {START_INTAKE, START_TECH_REVIEW, OPEN_PORTFOLIO}
    assert messages[0]["createSurface"]["surfaceId"] == "qualify-welcome-1"


def test_menu_card_validates() -> None:
    validate_surface(build_welcome_menu("qualify-welcome-1"))


def test_hello_shows_menu_instead_of_stage_one() -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-wm-1", user_text="hello"))

    assert "Welcome" in out.reply_text
    assert _events(out.a2ui_messages) == {START_INTAKE, START_TECH_REVIEW, OPEN_PORTFOLIO}
    assert not _has_stage_form(out.a2ui_messages)
    assert not out.session.rendered_stages


@pytest.mark.parametrize(
    "text",
    [
        "hello what can you do?",
        "Hi, what can you do",
        "hey there, how can you help?",
        "good morning, who are you?",
        "hello! what is this?",
        "ciao, help",
        "what can you do?",
        "hi team",
    ],
)
def test_greeting_or_help_question_opens_menu_not_intake(text: str) -> None:
    """Regression: "hello what can you do?" used to open the Stage 1 form."""
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-wm-help", user_text=text))

    assert _events(out.a2ui_messages) == {START_INTAKE, START_TECH_REVIEW, OPEN_PORTFOLIO}
    assert not _has_stage_form(out.a2ui_messages)
    assert not out.session.rendered_stages
    # One short intro over the card, not the long banner repeating it.
    assert "Pick one of the three workflows" in out.reply_text
    assert "I support three workflows" not in out.reply_text


@pytest.mark.parametrize(
    "text",
    [
        "hi, our claims team re-keys every invoice into SAP by hand",
        "hello, we want an agent that drafts replies to supplier queries",
        "start technical review",
    ],
)
def test_greeting_followed_by_content_is_not_swallowed(text: str) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-wm-content", user_text=text))
    assert "Pick one of the three workflows" not in out.reply_text


def test_opener_questions_mid_intake_reach_the_chat() -> None:
    """"what is this field for" during Stage 1 is a form question, not help."""
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-wm-mid", user_text="hello"))
    execute_turn(
        store,
        TurnInput(context_id="ctx-wm-mid", action_data={"name": START_INTAKE, "context": {}}),
    )
    out = execute_turn(
        store, TurnInput(context_id="ctx-wm-mid", user_text="what is this field for?")
    )
    assert START_INTAKE not in str(out.a2ui_messages)


def test_substantive_first_message_still_opens_intake() -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-wm-2",
            user_text="Our claims handlers re-key every invoice into SAP by hand",
        ),
    )
    assert _has_stage_form(out.a2ui_messages)


def test_intake_button_opens_stage_one() -> None:
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-wm-3", user_text="hello"))
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-wm-3", action_data={"name": START_INTAKE, "context": {}}),
    )
    assert _has_stage_form(out.a2ui_messages)
    assert "Stage 1" in out.reply_text
    assert 0 in out.session.rendered_stages

    # A second click on the old menu re-uses the open intake instead of resetting it.
    again = execute_turn(
        store,
        TurnInput(context_id="ctx-wm-3", action_data={"name": START_INTAKE, "context": {}}),
    )
    assert not again.a2ui_messages
    assert "already under way" in again.reply_text


def test_tech_review_button_without_record_lists_pending() -> None:
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-wm-4", user_text="hello"))
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-wm-4", action_data={"name": START_TECH_REVIEW, "context": {}}
        ),
    )
    assert out.reply_text
    assert out.reply_text != "Action received."
    assert not _has_stage_form(out.a2ui_messages)


def test_portfolio_button_without_records() -> None:
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-wm-5", user_text="hello"))
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-wm-5", action_data={"name": OPEN_PORTFOLIO, "context": {}}),
    )
    assert out.reply_text
    assert not _has_stage_form(out.a2ui_messages)


def test_menu_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WELCOME_MENU", "0")
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-wm-6", user_text="hello"))
    assert _has_stage_form(out.a2ui_messages)


def test_hi_over_the_a2a_wire_shows_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same path Gemini Enterprise takes: HTTP -> executor -> turn."""
    from tests.test_task_lifecycle import _envelope, _stream

    import qualify.agent.server as server_mod
    from starlette.testclient import TestClient

    def _no_llm(*_a, **_k):
        raise RuntimeError("LLM disabled for tests")

    monkeypatch.setattr(server_mod, "GeminiExtractionClient", _no_llm)
    monkeypatch.setattr(server_mod, "GeminiChatClient", _no_llm)
    app, _h, _p = server_mod.build_app()
    results, errors = _stream(
        TestClient(app), _envelope([{"kind": "text", "text": "Hi"}], "ctx-wm-wire")
    )
    assert not errors
    blob = str(results)
    assert START_INTAKE in blob
    assert "Stage 1 of 4" not in blob


def test_sharepoint_signin_moves_from_the_greeting_to_start_intake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SharePoint storage the sign-in is offered once, on the first reply
    (next to the menu), so the intake button goes straight to Stage 1."""
    from qualify.a2ui.signin import SIGNIN_SURFACE_ID

    monkeypatch.delenv("SIGNIN_CARD", raising=False)
    store = InMemorySessionStore(quiet=True)

    greet = execute_turn(store, TurnInput(context_id="ctx-wm-sp", user_text="hello"))
    assert _events(greet.a2ui_messages) == {START_INTAKE, START_TECH_REVIEW, OPEN_PORTFOLIO}
    assert greet.session.signin_prompted is True
    assert any(m.get("createSurface", {}).get("surfaceId") == SIGNIN_SURFACE_ID for m in greet.a2ui_messages)

    intake = execute_turn(
        store,
        TurnInput(context_id="ctx-wm-sp", action_data={"name": START_INTAKE, "context": {}}),
    )
    assert not any(m.get("createSurface", {}).get("surfaceId") == SIGNIN_SURFACE_ID for m in intake.a2ui_messages)
