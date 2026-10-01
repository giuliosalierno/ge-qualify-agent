"""Tests for the interactive side-panel views (portfolio, brief) and their buttons."""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.a2ui.actions import COMMIT_STAGE, REVISE_STAGE
from qualify.a2ui.validate import validate_surface
from qualify.a2ui.views.brief import build_brief_view
from qualify.a2ui.views.charts import (
    FEASIBILITY_THRESHOLD,
    VALUE_THRESHOLD,
    matrix_points,
    quadrant_matrix_spec,
    quadrant_regions,
)
from qualify.a2ui.views.events import OPEN_BRIEF, OPEN_PORTFOLIO, START_TECH_REVIEW
from qualify.a2ui.views.portfolio import build_portfolio_view
from qualify.agent.turn import TurnInput, execute_turn
from qualify.config import interactive_views_enabled
from qualify.connectors.sharepoint import SharePointConnector
from qualify.packs.loader import load_pack
from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import (
    Business,
    Meta,
    Network,
    Security,
    Sizing,
    Technical,
    UseCaseRecord,
)
from qualify.scoring.portfolio import _assign_quadrant, evaluate_portfolio
from qualify.sinks.session import InMemorySessionStore

from tests.test_brief_and_surfaces import (
    DATA_PAYLOAD,
    NEEDS_PAYLOAD,
    OWNERSHIP_PAYLOAD,
    SIZING_PAYLOAD,
)


@pytest.fixture(autouse=True)
def _views_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overrides the suite-wide default in conftest.py."""
    monkeypatch.setenv("INTERACTIVE_VIEWS", "1")


def _record(
    record_id: str,
    name: str,
    *,
    users: int = 100,
    freq: float = 5.0,
    saved: float = 30.0,
    level: CapabilityLevel = CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,
    blocker: str | None = None,
) -> UseCaseRecord:
    return UseCaseRecord(
        meta=Meta(record_id=record_id, initiative_name=name, department_bu="Finance"),
        business=Business(user_count=users, problem_description="Manual triage."),
        sizing=Sizing(
            task_frequency_weekly=freq,
            baseline_minutes_per_task=45.0,
            target_minutes_saved_per_task=saved,
        ),
        technical=Technical(
            data_sources=["sharepoint"],
            capability_level=level,
            network=Network(transit_blocker_status=blocker),
            security=Security(data_classification="internal"),
        ),
    )


def _portfolio() -> list[UseCaseRecord]:
    return [
        _record("UC-2026-QW0001", "Quick one", users=200),
        _record("UC-2026-QW0002", "Quick two", users=150),
        _record("UC-2026-SB0001", "Big bet", users=300, level=CapabilityLevel.HIGH_CODE_AGENT),
        _record("UC-2026-DP0001", "Small", users=1, freq=1, saved=5),
    ]


def _summary():
    return evaluate_portfolio([(r, {"hasBrief": True}) for r in _portfolio()])


def _components(messages: list[dict]) -> dict[str, dict]:
    update = next(m["updateComponents"] for m in messages if "updateComponents" in m)
    return {c["id"]: c for c in update["components"]}


def _data(messages: list[dict]) -> dict:
    return next(m["updateDataModel"]["value"] for m in messages if "updateDataModel" in m)


# ---------------------------------------------------------------------------
# Flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [("1", True), ("", True), ("0", False), ("off", False)])
def test_flag(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("INTERACTIVE_VIEWS", value)
    assert interactive_views_enabled() is (expected if value else True)


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------


def test_points_sharing_a_cell_are_spread_deterministically() -> None:
    evs = _summary().evaluations
    a, b = matrix_points(evs), matrix_points(evs)
    assert a == b
    shared = [p for p in a if p["recordId"].startswith("UC-2026-QW")]
    assert len({(p["x"], p["y"]) for p in shared}) == len(shared)
    for p in a:
        # Inside its own cell, hence inside its own quadrant region.
        assert abs(p["x"] - p["feasibility"]) < 0.5
        assert abs(p["y"] - p["value"]) < 0.5


def _region_at(x: float, y: float) -> str:
    hits = [
        r["quadrant"]
        for r in quadrant_regions()
        if r["x"] <= x <= r["x2"] and r["y"] <= y <= r["y2"]
    ]
    assert len(hits) == 1, (x, y, hits)
    return hits[0]


@pytest.mark.parametrize("feas", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_drawn_regions_match_the_scoring_for_every_cell(feas: int, value: int) -> None:
    """The shading must say what the scoring says, for every score pair."""
    assert _region_at(feas, value) == _assign_quadrant(value, feas, False)
    # And for the furthest a spread point can sit from the cell centre.
    for dx, dy in ((0.28, 0.28), (-0.28, -0.28), (0.28, -0.28), (-0.28, 0.28)):
        assert _region_at(feas + dx, value + dy) == _assign_quadrant(value, feas, False)


def test_blocked_points_are_labelled() -> None:
    evs = evaluate_portfolio(
        [(_record("UC-2026-BL0001", "Blocked one", blocker="blocked"), {"hasBrief": True})]
    ).evaluations
    (point,) = matrix_points(evs)
    assert point["blocked"] is True
    assert point["label"].startswith("⛔")


def test_chart_thresholds_match_the_scoring() -> None:
    assert _assign_quadrant(VALUE_THRESHOLD, FEASIBILITY_THRESHOLD, False) == "Quick Wins"
    assert _assign_quadrant(VALUE_THRESHOLD - 1, FEASIBILITY_THRESHOLD, False) != "Quick Wins"
    assert _assign_quadrant(VALUE_THRESHOLD, FEASIBILITY_THRESHOLD - 1, False) != "Quick Wins"


def test_highlight_marks_exactly_one_point() -> None:
    evs = _summary().evaluations
    points = matrix_points(evs, highlight_id="UC-2026-SB0001")
    assert [p["recordId"] for p in points if p["highlight"]] == ["UC-2026-SB0001"]
    labels = quadrant_matrix_spec(points)["layer"][-1]
    assert labels["encoding"]["text"] == {"field": "label"}
    assert labels["encoding"]["fontWeight"]["condition"]["test"] == "datum.highlight"


# ---------------------------------------------------------------------------
# Portfolio view
# ---------------------------------------------------------------------------


def test_portfolio_view_validates_and_is_a_canvas() -> None:
    messages = build_portfolio_view(_summary(), "qualify-portfolio-1")
    validate_surface(messages)
    by_id = _components(messages)
    assert by_id["root"]["component"] == "Canvas"
    assert [t["title"] for t in by_id["pf-tabs"]["tabs"]] == ["Matrix", "Ranked list", "Actions"]
    assert by_id["pf-chart"]["spec"] == {"path": "/ui/portfolio/chart"}
    assert len(_data(messages)["ui"]["portfolio"]["chart"]["layer"][4]["data"]["values"]) == 4


def test_portfolio_table_cells_are_strings_in_rank_order() -> None:
    summary = _summary()
    rows = _components(build_portfolio_view(summary, "s"))["pf-table"]["rows"]
    assert [r["recordId"] for r in rows] == [e.record_id for e in summary.evaluations]
    assert all(isinstance(v, str) for r in rows for v in r.values())


def test_portfolio_table_headers_are_not_sortable() -> None:
    # In GE a sortable header sends a `fetchData` action, i.e. a chat turn.
    columns = _components(build_portfolio_view(_summary(), "s"))["pf-table"]["columns"]
    assert columns and not any(c["sortable"] for c in columns)


def test_table_fetch_data_from_an_old_panel_is_handled_quietly() -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-v6", action_data={"name": "fetchData", "context": {}}),
    )
    assert "sorting" in out.reply_text
    assert out.a2ui_messages == []


def test_portfolio_actions_offer_brief_and_review_buttons() -> None:
    by_id = _components(build_portfolio_view(_summary(), "s"))
    events = [
        (c["action"]["event"]["name"], c["action"]["event"]["context"].get("recordId"))
        for c in by_id.values()
        if c["component"] == "Button"
    ]
    assert (OPEN_BRIEF, "UC-2026-SB0001") in events
    assert (START_TECH_REVIEW, "UC-2026-SB0001") in events


def test_empty_portfolio_still_validates() -> None:
    validate_surface(build_portfolio_view(evaluate_portfolio([]), "s"))


# ---------------------------------------------------------------------------
# Brief view
# ---------------------------------------------------------------------------


def test_brief_view_validates_with_and_without_pack() -> None:
    rec = _record("UC-2026-BR0001", "Brief me")
    with_pack = build_brief_view(rec, "s", pack=load_pack("business"))
    without = build_brief_view(rec, "s")
    validate_surface(with_pack)
    validate_surface(without)

    def revise(msgs: list[dict]) -> list[str]:
        return [
            c["action"]["event"]["context"]["stage"]
            for c in _components(msgs).values()
            if c["component"] == "Button" and c["action"]["event"]["name"] == REVISE_STAGE
        ]

    assert revise(with_pack) == [s.id for s in load_pack("business").stages]
    assert revise(without) == []


def test_brief_chart_highlights_this_record_among_the_portfolio() -> None:
    rec = _portfolio()[0]
    messages = build_brief_view(rec, "s", portfolio=list(_summary().evaluations))
    points = _data(messages)["ui"]["brief"]["chart"]["layer"][4]["data"]["values"]
    assert len(points) == 4
    assert [p["recordId"] for p in points if p["highlight"]] == [rec.meta.record_id]


def test_brief_view_does_not_mutate_scoring() -> None:
    rec = _record("UC-2026-BR0002", "Pure")
    before = rec.scoring.model_dump()
    build_brief_view(rec, "s")
    assert rec.scoring.model_dump() == before


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


@pytest.fixture
def sharepoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SharePointConnector:
    mock = SharePointConnector(mock_dir=tmp_path / "sp")
    monkeypatch.setattr("qualify.connectors.sharepoint.get_sharepoint_connector", lambda: mock)
    for r in _portfolio():
        mock.sync_opportunity(r, pack_name="business")
    return mock


def test_portfolio_review_opens_the_side_panel(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-v1", user_text="portfolio review"))
    assert "opened in the side panel" in out.reply_text
    assert "AI CoE Portfolio Prioritization Report" not in out.reply_text
    validate_surface(out.a2ui_messages)
    assert _components(out.a2ui_messages)["root"]["component"] == "Canvas"
    assert len(out.session.pending_review_choices) == 4


def test_flag_off_restores_markdown(sharepoint, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERACTIVE_VIEWS", "0")
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-v2", user_text="portfolio review"))
    assert "AI CoE Portfolio Prioritization Report" in out.reply_text
    assert out.a2ui_messages == []


def test_open_portfolio_button(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-v3", action_data={"name": OPEN_PORTFOLIO, "context": {}}),
    )
    assert _components(out.a2ui_messages)["root"]["cardTitle"] == "Portfolio prioritization"


def test_start_tech_review_button_matches_the_typed_command(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-v4",
            action_data={"name": START_TECH_REVIEW, "context": {"recordId": "UC-2026-SB0001"}},
        ),
    )
    assert out.session.pack_name == "tech"
    assert out.session.record.meta.record_id == "UC-2026-SB0001"


def test_open_brief_for_unknown_record_says_so() -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-v5",
            action_data={"name": OPEN_BRIEF, "context": {"recordId": "UC-2026-NOPE00"}},
        ),
    )
    assert "couldn't find" in out.reply_text
    assert out.a2ui_messages == []


def test_finishing_phase_one_opens_the_brief_panel() -> None:
    store = InMemorySessionStore(quiet=True)
    ctx = "ctx-v6"
    execute_turn(store, TurnInput(context_id=ctx, user_text="Start"))
    out = None
    for payload in (NEEDS_PAYLOAD, SIZING_PAYLOAD, DATA_PAYLOAD, OWNERSHIP_PAYLOAD):
        out = execute_turn(
            store,
            TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": payload}),
        )
    assert out is not None
    assert "Business Value Brief ready" in out.reply_text
    assert "# Business Value Brief:" not in out.reply_text
    validate_surface(out.a2ui_messages)
    by_id = _components(out.a2ui_messages)
    assert by_id["root"]["component"] == "Canvas"
    assert "AP Invoice Exception Assistant" in by_id["br-title"]["text"]
    assert "5,000" in by_id["root"]["cardDescription"]


def test_views_use_only_components_seen_rendering_in_ge() -> None:
    """A component that is in the catalog but never watched in GE can render
    nothing at all; the views must not depend on one."""
    from qualify.a2ui.catalog import GE_RENDER_VERIFIED

    surfaces = [
        build_portfolio_view(_summary(), "s"),
        build_brief_view(_portfolio()[0], "s", pack=load_pack("business")),
    ]
    used = {c["component"] for msgs in surfaces for c in _components(msgs).values()}
    assert used <= GE_RENDER_VERIFIED, used - GE_RENDER_VERIFIED


def test_chart_has_a_fixed_size() -> None:
    """`width: container` rendered narrow on first open in GE's side panel."""
    spec = quadrant_matrix_spec(matrix_points(_summary().evaluations))
    assert isinstance(spec["width"], int) and isinstance(spec["height"], int)
    assert spec["autosize"]["type"] == "fit"
