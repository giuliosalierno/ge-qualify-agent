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
    assert [t["title"] for t in by_id["pf-tabs"]["tabs"]] == ["Ranked list", "Matrix", "Actions"]
    assert by_id["pf-chart"]["spec"] == {"path": "/ui/portfolio/chart"}
    assert len(_data(messages)["ui"]["portfolio"]["chart"]["layer"][4]["data"]["values"]) == 4


def test_charts_are_never_on_the_first_tab() -> None:
    """GE sizes a chart on the first tab to the half-open panel (narrow render)."""
    for messages, tabs_id in (
        (build_portfolio_view(_summary(), "s"), "pf-tabs"),
        (build_brief_view(_summary().evaluations[0].record, "b"), "br-tabs"),
    ):
        by_id = _components(messages)
        first = by_id[by_id[tabs_id]["tabs"][0]["child"]]
        stack, seen = [first], set()
        while stack:
            node = stack.pop()
            assert node["component"] != "VegaChart", f"chart on first tab of {tabs_id}"
            for cid in node.get("children", []) if isinstance(node.get("children"), list) else []:
                if cid in by_id and cid not in seen:
                    seen.add(cid)
                    stack.append(by_id[cid])


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


def test_ranked_list_explains_the_scoring() -> None:
    by_id = _components(build_portfolio_view(_summary(), "s"))
    assert by_id["pf-tabs"]["tabs"][0]["child"] == "pf-ranked"
    children = by_id["pf-ranked"]["children"]
    assert children == ["pf-table", "pf-how-panel"]
    panel = by_id["pf-how-panel"]
    assert panel["component"] == "MaterialExpansionPanel"
    assert panel["expanded"] is False
    panel_children = panel["children"]
    text = " ".join(by_id[c].get("text", "") for c in panel_children if "text" in by_id[c])
    for term in ("Value (1–5)", "Feasibility (1–5)", "indicative", "Quadrant", "60% value"):
        assert term in text
    # GE renders markdown in body Text but shows it raw in caption.
    for c in panel_children:
        if by_id[c]["component"] == "Text":
            assert by_id[c].get("variant") != "caption", c
    assert "\\*" not in text


def test_value_note_matches_the_scoring_cut_offs() -> None:
    """The note's hour cut-offs are where the integer score actually changes."""
    from qualify.scoring.portfolio import _score_business_value

    def value_for(hours: float) -> int:
        # derived hours = freq * minutes / 60 * 50 weeks * users; 1 user, 1/wk.
        rec = UseCaseRecord(meta=Meta(record_id="UC-2026-CUT001", initiative_name="x"))
        rec.business.user_count = 1
        rec.sizing.task_frequency_weekly = 1
        rec.sizing.target_minutes_saved_per_task = hours * 60 / 50
        return _score_business_value(rec)[0]

    for hours, expected in ((30, 1), (40, 2), (340, 2), (360, 3), (1_740, 3), (1_760, 4), (7_400, 4), (7_600, 5)):
        assert value_for(hours) == expected, hours


# ---------------------------------------------------------------------------
# Open-in-storage buttons
# ---------------------------------------------------------------------------


def _open_urls(by_id: dict[str, dict]) -> dict[str, str]:
    return {
        cid: c["action"]["functionCall"]["args"]["url"]
        for cid, c in by_id.items()
        if c["component"] == "MaterialButton" and "functionCall" in c.get("action", {})
    }


def test_portfolio_rows_open_their_sharepoint_folder(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-v-open", user_text="portfolio review"))
    validate_surface(out.a2ui_messages)
    by_id = _components(out.a2ui_messages)
    urls = _open_urls(by_id)
    assert len(urls) == 4
    assert all("sharepoint" in u for u in urls.values())
    assert all(by_id[cid]["label"] == "Open in SharePoint" for cid in urls)
    # The opportunity name is the text-link fallback.
    first = next(iter(urls))
    assert urls[first] in by_id[first.replace("-open", "-name")]["text"]


def test_portfolio_without_folder_urls_has_no_open_buttons() -> None:
    assert _open_urls(_components(build_portfolio_view(_summary(), "s"))) == {}


def test_brief_view_open_buttons_only_with_links() -> None:
    rec = _record("UC-2026-BR0003", "Linked")
    assert _open_urls(_components(build_brief_view(rec, "s"))) == {}
    msgs = build_brief_view(
        rec, "s", links={"folder": "https://t.sharepoint.com/f", "business": "https://t.sharepoint.com/f/b.md"}
    )
    validate_surface(msgs)
    by_id = _components(msgs)
    assert _open_urls(by_id) == {
        "br-open-folder": "https://t.sharepoint.com/f",
        "br-open-brief": "https://t.sharepoint.com/f/b.md",
    }
    assert by_id["br-open-folder"]["label"] == "Open folder in SharePoint"
    assert "br-open-folder-link" in by_id["br-next"]["children"]


def test_open_brief_event_adds_the_sharepoint_buttons(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-v-brief",
            action_data={"name": OPEN_BRIEF, "context": {"recordId": "UC-2026-SB0001"}},
        ),
    )
    urls = _open_urls(_components(out.a2ui_messages))
    assert "UC-2026-SB0001" in urls["br-open-folder"]
    assert urls["br-open-brief"].startswith(urls["br-open-folder"])


# ---------------------------------------------------------------------------
# Not signed in to storage: say so, and remember folder addresses
# ---------------------------------------------------------------------------


def _finished_in(store, record: UseCaseRecord, folder: str | None = None) -> None:
    from qualify.sinks.session import Session

    session = Session(context_id=f"ctx-{record.meta.record_id}", pack_name="business", record=record)
    session.committed = set(range(len(session.pack.stages)))
    if folder:
        session.document_links["folder"] = folder
    store.save(session)


class _SignedOut:
    display_name = "Microsoft SharePoint"
    account_label = "Microsoft"

    def load_all_opportunities(self, *a, **k):
        from qualify.connectors.storage import StorageAuthRequired

        raise StorageAuthRequired("sign in")

    def list_opportunities(self, *a, **k):
        from qualify.connectors.storage import StorageAuthRequired

        raise StorageAuthRequired("sign in")


def test_signed_out_portfolio_offers_sign_in_and_uses_remembered_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualify.sinks.record_store import LocalRecordStore

    monkeypatch.setattr("qualify.connectors.sharepoint.get_sharepoint_connector", lambda: _SignedOut())
    store = LocalRecordStore(tmp_path / "rs")
    recs = _portfolio()
    _finished_in(store, recs[0], folder="https://t.sharepoint.com/sites/x/Q/UC-1")
    _finished_in(store, recs[1])

    out = execute_turn(store, TurnInput(context_id="ctx-v-so", user_text="portfolio review"))
    validate_surface(out.a2ui_messages)
    assert "not signed in" in out.reply_text and "Sign in with Microsoft" in out.reply_text
    assert out.session.resume_command == "portfolio review"
    by_id = _components(out.a2ui_messages)
    assert by_id["pf-signin"]["action"]["functionCall"]["call"] == "openUrl"
    assert "pf-signin" in by_id["root"]["children"]
    # The folder saved with the first record still opens without a sign-in.
    assert list(_open_urls(by_id).values()).count("https://t.sharepoint.com/sites/x/Q/UC-1") == 1


def test_signed_in_portfolio_has_no_sign_in_button(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-v-si", user_text="portfolio review"))
    assert "pf-signin" not in _components(out.a2ui_messages)
    assert "not signed in" not in out.reply_text


def test_signed_in_portfolio_backfills_folder_urls(sharepoint, tmp_path: Path) -> None:
    from qualify.sinks.record_store import LocalRecordStore

    store = LocalRecordStore(tmp_path / "rs2")
    for r in _portfolio():
        _finished_in(store, r)
    execute_turn(store, TurnInput(context_id="ctx-v-bf", user_text="portfolio review"))
    urls = {e["recordId"]: e["webUrl"] for e in store.list_completed()}
    assert urls and all(u and "sharepoint" in u for u in urls.values())


def test_signed_out_tech_review_links_come_from_the_record_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualify.agent.handover import review_document_links
    from qualify.sinks.record_store import LocalRecordStore

    monkeypatch.setattr("qualify.connectors.sharepoint.get_sharepoint_connector", lambda: _SignedOut())
    store = LocalRecordStore(tmp_path / "rs3")
    rec = _portfolio()[0]
    _finished_in(store, rec, folder="https://t.sharepoint.com/sites/x/Q/F")
    links = review_document_links(rec.meta.record_id, store=store)
    assert links["folder"] == "https://t.sharepoint.com/sites/x/Q/F"
    assert links["business"].startswith("https://t.sharepoint.com/sites/x/Q/F/")
