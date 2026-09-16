"""Tests for the SharePoint sign-in A2UI surfaces."""

from __future__ import annotations

from qualify.a2ui.catalog import catalog_id, function_names
from qualify.a2ui.signin import SIGNIN_SURFACE_ID, build_openurl_probe
from qualify.a2ui.validate import validate_surface
from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.session import InMemorySessionStore

_AUTH_URL = "https://ge-qualify-agent.example.run.app/auth?context_id=ctx-1"


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


def test_probe_command_returns_the_surface_without_touching_sharepoint() -> None:
    """The trigger contains 'sign in', which the SharePoint matcher also claims.

    Ordering in execute_turn decides this, so a regression would silently send
    the user an auth prompt instead of the probe.
    """
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
