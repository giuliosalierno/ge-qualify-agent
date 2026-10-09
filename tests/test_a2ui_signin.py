"""Tests for the SharePoint sign-in A2UI surfaces."""

from __future__ import annotations

import pytest

from qualify.a2ui.actions import DISMISS_SIGNIN
from qualify.a2ui.catalog import catalog_id, function_names
from qualify.a2ui.signin import (
    SIGNIN_SURFACE_ID,
    build_openurl_probe,
    build_signin_card,
)
from qualify.a2ui.validate import validate_surface
from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.session import InMemorySessionStore

_AUTH_URL = "https://ge-qualify-agent.example.run.app/auth?context_id=ctx-1"


@pytest.fixture(autouse=True)
def _enable_signin_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overrides the suite-wide default in conftest.py.

    These are the tests that exist to exercise the card, so switching it back
    on here rather than removing the global default keeps every other test
    unaffected by it.
    """
    monkeypatch.delenv("SIGNIN_CARD", raising=False)


def test_openurl_is_declared_by_the_gemini_enterprise_catalog() -> None:
    """The probe is only worth running if GE's own catalog admits the function.

    This does not prove the renderer implements it, which is exactly why the
    probe exists, but it does prove we are not inventing a function name.
    """
    assert "openUrl" in function_names()


def test_openurl_probe_validates_against_the_ge_catalog() -> None:
    messages = build_openurl_probe(_AUTH_URL)
    validate_surface(messages)

    create, update = messages
    assert create["createSurface"]["surfaceId"] == SIGNIN_SURFACE_ID
    assert create["createSurface"]["catalogId"] == catalog_id()
    assert update["updateComponents"]["surfaceId"] == SIGNIN_SURFACE_ID


def test_openurl_probe_offers_three_independent_renderings() -> None:
    """Base Button, MaterialButton, and a markdown control must all be present.

    One round trip has to distinguish "the surface failed" from "this component
    failed" from "openUrl was ignored", so losing any of the three makes the
    result ambiguous.
    """
    components = build_openurl_probe(_AUTH_URL)[1]["updateComponents"]["components"]
    by_id = {c["id"]: c for c in components}

    expected_action = {
        "functionCall": {"call": "openUrl", "args": {"url": _AUTH_URL}}
    }

    base = by_id["probe-base-button"]
    assert base["component"] == "Button"
    assert base["action"] == expected_action
    assert base["child"] == "probe-base-label"

    material = by_id["probe-material-button"]
    assert material["component"] == "MaterialButton"
    assert material["action"] == expected_action
    assert material["label"] == "Sign in with Microsoft"

    control = by_id["probe-control-link"]
    assert control["component"] == "Text"
    assert f"({_AUTH_URL})" in control["text"]


def test_probe_command_returns_the_surface_without_touching_sharepoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trigger contains 'sign in', which the SharePoint matcher also claims.

    Ordering in execute_turn decides this, so a regression would silently send
    the user an auth prompt instead of the probe.
    """
    monkeypatch.setenv("A2UI_PROBES", "1")
    store = InMemorySessionStore(quiet=True)
    output = execute_turn(
        store, TurnInput(context_id="ctx-probe", user_text="a2ui probe openurl")
    )

    assert "openUrl" in output.reply_text
    assert output.auth_required is False
    assert len(output.a2ui_messages) == 2
    assert output.a2ui_messages[0]["createSurface"]["surfaceId"] == SIGNIN_SURFACE_ID


def test_probe_command_does_not_fire_on_ordinary_conversation() -> None:
    """An exact-match trigger keeps the probe out of real qualifications."""
    store = InMemorySessionStore(quiet=True)
    output = execute_turn(
        store,
        TurnInput(
            context_id="ctx-normal",
            user_text="We want to probe openurl latency for our new API",
        ),
    )
    assert "A2UI `openUrl` probe" not in output.reply_text


# ---------------------------------------------------------------------------
# The production sign-in card
# ---------------------------------------------------------------------------


def test_signin_card_validates_and_uses_a_material_button() -> None:
    messages = build_signin_card(_AUTH_URL)
    validate_surface(messages)

    by_id = {c["id"]: c for c in messages[1]["updateComponents"]["components"]}

    connect = by_id["signin-connect"]
    assert connect["component"] == "MaterialButton"
    assert connect["action"]["functionCall"]["call"] == "openUrl"
    assert connect["action"]["functionCall"]["args"]["url"] == _AUTH_URL


def test_signin_card_keeps_a_markdown_fallback_beside_the_button() -> None:
    """openUrl is a client function, so a silent failure would strand the user.

    If a future GE release stops honouring it the button does nothing and
    reports nothing. The text link is the only remaining way in, so losing it
    would turn a cosmetic regression into a broken feature.
    """
    by_id = {
        c["id"]: c for c in build_signin_card(_AUTH_URL)[1]["updateComponents"]["components"]
    }
    assert f"({_AUTH_URL})" in by_id["signin-fallback"]["text"]


def test_signin_card_skip_button_dispatches_to_the_server() -> None:
    """The skip button must use an event, not a client function.

    The server has to learn about the choice to stop re-offering the card.
    """
    by_id = {
        c["id"]: c for c in build_signin_card(_AUTH_URL)[1]["updateComponents"]["components"]
    }
    assert by_id["signin-skip"]["action"]["event"]["name"] == DISMISS_SIGNIN


def test_signin_card_is_offered_on_the_opening_turn() -> None:
    store = InMemorySessionStore(quiet=True)
    output = execute_turn(
        store, TurnInput(context_id="ctx-signin", user_text="I have a new use case")
    )

    assert "Microsoft SharePoint" in output.reply_text
    assert output.a2ui_messages[0]["createSurface"]["surfaceId"] == SIGNIN_SURFACE_ID
    assert output.session.signin_prompted is True


def test_signin_card_is_offered_only_once() -> None:
    """A second turn must start the interview rather than re-ask.

    The user stays unauthenticated while they ignore the card, so without the
    `signin_prompted` latch the condition would still hold and the card would
    reappear on every single turn.
    """
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-once", user_text="new use case"))
    second = execute_turn(
        store, TurnInput(context_id="ctx-once", user_text="it is about invoices")
    )

    surfaces = [
        m["createSurface"]["surfaceId"] for m in second.a2ui_messages if "createSurface" in m
    ]
    assert SIGNIN_SURFACE_ID not in surfaces


def test_signin_card_is_skipped_when_a_token_is_already_vaulted() -> None:
    import qualify.connectors.sharepoint as sp_mod

    sp_mod._TOKEN_VAULT.clear()
    sp_mod.cache_delegated_token("eyJ_already_signed_in", key="ctx-has-token")

    store = InMemorySessionStore(quiet=True)
    output = execute_turn(
        store, TurnInput(context_id="ctx-has-token", user_text="new use case")
    )

    surfaces = [
        m["createSurface"]["surfaceId"] for m in output.a2ui_messages if "createSurface" in m
    ]
    assert SIGNIN_SURFACE_ID not in surfaces
    sp_mod._TOKEN_VAULT.clear()


def test_signin_card_can_be_disabled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNIN_CARD", "0")
    store = InMemorySessionStore(quiet=True)
    output = execute_turn(
        store, TurnInput(context_id="ctx-disabled", user_text="new use case")
    )

    surfaces = [
        m["createSurface"]["surfaceId"] for m in output.a2ui_messages if "createSurface" in m
    ]
    assert SIGNIN_SURFACE_ID not in surfaces


def test_dismissing_signin_opens_stage_one_without_losing_progress() -> None:
    """Declining must start the interview, not just acknowledge the click."""
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-dismiss", user_text="new use case"))

    after = execute_turn(
        store,
        TurnInput(
            context_id="ctx-dismiss",
            action_data={"name": DISMISS_SIGNIN, "context": {}},
        ),
    )

    assert after.session.signin_dismissed is True
    assert after.session.active_stage == 0
    surfaces = [
        m["createSurface"]["surfaceId"] for m in after.a2ui_messages if "createSurface" in m
    ]
    assert surfaces and SIGNIN_SURFACE_ID not in surfaces

