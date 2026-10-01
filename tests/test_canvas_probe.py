"""Tests for the side-panel canvas probe (``probe canvas``)."""

from __future__ import annotations

from qualify.a2ui.canvas_probe import (
    FULL_SURFACE_ID,
    INLINE_SURFACE_ID,
    MIN_SURFACE_ID,
    PROBE_CANVAS_ECHO,
    build_canvas_probe,
    describe_echo,
    is_probe_trigger,
)
from qualify.a2ui.catalog import component_names
from qualify.a2ui.validate import validate_surface
from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.session import InMemorySessionStore


def _components(messages: list[dict], surface_id: str) -> dict[str, dict]:
    for m in messages:
        update = m.get("updateComponents")
        if update and update["surfaceId"] == surface_id:
            return {c["id"]: c for c in update["components"]}
    raise AssertionError(f"no updateComponents for {surface_id}")


def test_probe_validates_against_the_ge_catalog() -> None:
    validate_surface(build_canvas_probe())


def test_probe_uses_only_components_the_catalog_declares() -> None:
    declared = component_names()
    for m in build_canvas_probe():
        for c in (m.get("updateComponents") or {}).get("components", []):
            assert c["component"] in declared, c["component"]


def test_three_independent_surfaces_each_created_before_use() -> None:
    """One failing component must not blank the others, so each test is its
    own surface, and each surface is created before it is filled."""
    messages = build_canvas_probe()
    seen: list[str] = []
    for m in messages:
        if "createSurface" in m:
            seen.append(m["createSurface"]["surfaceId"])
        for key in ("updateComponents", "updateDataModel"):
            if key in m:
                assert m[key]["surfaceId"] in seen
    assert seen == [MIN_SURFACE_ID, FULL_SURFACE_ID, INLINE_SURFACE_ID]


def test_canvas_surfaces_are_rooted_in_canvas() -> None:
    """The catalog requires Canvas to be the root of its surface."""
    messages = build_canvas_probe()
    assert _components(messages, MIN_SURFACE_ID)["root"]["component"] == "Canvas"
    assert _components(messages, FULL_SURFACE_ID)["root"]["component"] == "Canvas"
    assert _components(messages, INLINE_SURFACE_ID)["root"]["component"] == "Column"


def test_save_button_binds_the_slider_and_text_field() -> None:
    by_id = _components(build_canvas_probe(), FULL_SURFACE_ID)
    event = by_id["p2-save"]["action"]["event"]
    assert event["name"] == PROBE_CANVAS_ECHO
    assert event["context"]["name"] == by_id["p2-name"]["value"]
    assert event["context"]["users"] == by_id["p2-users"]["value"]


def test_trigger_is_exact_match_only() -> None:
    assert is_probe_trigger("probe canvas")
    assert is_probe_trigger("  Probe Canvas ")
    assert not is_probe_trigger("we should probe canvas adoption in marketing")
    assert not is_probe_trigger(None)


def test_echo_reports_value_types() -> None:
    text = describe_echo({"name": "Claims", "users": 340, "prompt": "Save"})
    assert "'Claims'` (str)" in text
    assert "`340` (int)" in text


def test_turn_renders_probe_on_trigger() -> None:
    store = InMemorySessionStore(quiet=True)
    output = execute_turn(store, TurnInput(context_id="ctx-cp", user_text="probe canvas"))
    assert "canvas probe" in output.reply_text
    assert output.a2ui_messages == build_canvas_probe()


def test_turn_echoes_save_without_touching_the_record() -> None:
    store = InMemorySessionStore(quiet=True)
    execute_turn(store, TurnInput(context_id="ctx-cp2", user_text="probe canvas"))
    before = store.load("ctx-cp2")
    output = execute_turn(
        store,
        TurnInput(
            context_id="ctx-cp2",
            action_data={
                "name": PROBE_CANVAS_ECHO,
                "surfaceId": FULL_SURFACE_ID,
                "context": {"name": "Edited", "users": 500},
            },
        ),
    )
    assert "Save received" in output.reply_text
    assert "`500` (int)" in output.reply_text
    after = store.load("ctx-cp2")
    assert (before.record if before else None) == (after.record if after else None)
