"""Tests for the opportunity workspace side panel and the live-refresh probe."""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.a2ui.actions import COMMIT_STAGE, REVISE_STAGE, SKIP_STAGE
from qualify.a2ui.canvas_probe import (
    LIVE_SURFACE_ID,
    PROBE_LIVE_BUMP,
    build_live_bump,
    build_live_probe,
)
from qualify.a2ui.validate import validate_surface
from qualify.a2ui.views.events import OPEN_WORKSPACE, START_TECH_REVIEW
from qualify.a2ui.views.workspace import (
    add_open_workspace_button,
    build_workspace_view,
    markdown_sections,
    stage_statuses,
)
from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors.sharepoint import SharePointConnector
from qualify.packs.loader import load_pack
from qualify.sinks.record_store import _session_from_dict, _session_to_dict
from qualify.sinks.session import InMemorySessionStore, new_session

from tests.test_brief_and_surfaces import (
    DATA_PAYLOAD,
    NEEDS_PAYLOAD,
    OWNERSHIP_PAYLOAD,
    SIZING_PAYLOAD,
)
from tests.test_views import _components, _portfolio


@pytest.fixture(autouse=True)
def _views_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERACTIVE_VIEWS", "1")


@pytest.fixture
def sharepoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SharePointConnector:
    mock = SharePointConnector(mock_dir=tmp_path / "sp")
    monkeypatch.setattr("qualify.connectors.sharepoint.get_sharepoint_connector", lambda: mock)
    for r in _portfolio():
        mock.sync_opportunity(r, pack_name="business")
    return mock


def _view(pack_name: str = "business", **kw):
    record = _portfolio()[0]
    args = dict(
        pack=load_pack(pack_name),
        record=record,
        committed={0},
        skipped=set(),
        active_stage=1,
        surface_id="ws",
    )
    args.update(kw)
    return build_workspace_view(**args)


def _buttons(by_id: dict[str, dict]) -> list[tuple[str, dict]]:
    return [
        (c["action"]["event"]["name"], c["action"]["event"]["context"])
        for c in by_id.values()
        if c["component"] == "Button"
    ]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack_name", ["business", "tech"])
def test_workspace_validates_for_both_packs(pack_name: str) -> None:
    validate_surface(_view(pack_name))
    validate_surface(_view(pack_name, committed={0, 1, 2}, skipped={2}, complete=True))


def test_business_workspace_has_progress_and_documents_tabs() -> None:
    by_id = _components(_view())
    assert by_id["root"]["component"] == "Canvas"
    assert [t["title"] for t in by_id["ws-tabs"]["tabs"]] == ["Progress", "Documents"]


def test_tech_workspace_adds_the_checklist_tab() -> None:
    by_id = _components(_view("tech"))
    assert [t["title"] for t in by_id["ws-tabs"]["tabs"]] == ["Progress", "Checklist", "Documents"]
    assert "Technical readiness" in by_id["ws-ck-head"]["text"]
    dims = [k for k in by_id if k.startswith("ws-ck-d") and not k.endswith("-body")]
    assert len(dims) == 5


def test_focus_puts_that_tab_first() -> None:
    by_id = _components(_view("tech", focus="documents"))
    assert by_id["ws-tabs"]["tabs"][0]["title"] == "Documents"


def test_stage_states_and_buttons() -> None:
    pack = load_pack("business")
    record = _portfolio()[0]
    sts = stage_statuses(pack, record, committed={0, 2}, skipped={2}, active_stage=1, complete=False)
    assert [s.state for s in sts] == ["confirmed", "current", "skipped", "todo"]

    by_id = _components(_view(committed={0, 2}, skipped={2}, active_stage=1))
    labels = {
        c["id"]: by_id[c["child"]]["text"] for c in by_id.values() if c["component"] == "Button"
    }
    assert labels["ws-st-needs-btn"] == "Revise answers"
    assert labels["ws-st-sizing-btn"] == "Show the form in chat"
    assert labels["ws-st-data-btn"] == "Fill now"
    assert by_id["ws-st-ownership-btn"]["component"] == "Text"  # not started: no button
    # Every stage button reopens that stage in this pack.
    for name, ctx in _buttons(by_id):
        if name == REVISE_STAGE:
            assert ctx["pack"] == "business" and ctx["stage"] in {"needs", "sizing", "data"}


def test_missing_required_fields_are_listed() -> None:
    by_id = _components(_view())
    text = by_id["ws-pg-missing"]["text"]
    assert "Missing before you submit" in text
    assert "Who does this work" in text  # stage 1, confirmed with a gap
    assert "Your name" not in text  # stage 4 not reached: summarised instead
    assert "2 more stages not started yet" in text


def test_answers_use_option_labels() -> None:
    by_id = _components(_view())
    body = by_id["ws-st-data-body"]["text"]
    assert "SharePoint" in body and "sharepoint" not in body


def test_documents_preview_in_sections_and_link_when_stored() -> None:
    by_id = _components(
        _view(links={"folder": "https://drive/f", "business": "https://drive/b"}, storage_label="Google Drive")
    )
    assert by_id["ws-doc-folder"]["component"] == "MaterialButton"
    assert by_id["ws-doc-folder"]["action"] == {
        "functionCall": {"call": "openUrl", "args": {"url": "https://drive/f"}}
    }
    assert "https://drive/f" in by_id["ws-doc-folder-link"]["text"]  # text fallback
    assert by_id["ws-doc-brief-open"]["label"] == "Open in Google Drive"
    assert "https://drive/b" in by_id["ws-doc-brief-open-link"]["text"]
    assert "ws-doc-brief-open" in by_id["ws-doc-brief"]["children"]
    assert by_id["ws-doc-brief"]["description"].startswith("Draft")
    headings = [c["text"] for k, c in by_id.items() if k.startswith("ws-doc-brief-s") and k.endswith("-h")]
    assert any("Summary" in h for h in headings)
    # The dossier does not exist before the technical review.
    assert by_id["ws-doc-dossier"]["description"].startswith("Not started")


def test_demo_mode_explains_there_are_no_files() -> None:
    by_id = _components(_view())
    assert "storage is off" in by_id["ws-doc-note"]["text"]
    assert not any(k.endswith("-link") for k in by_id)
    assert not any(c["component"] == "MaterialButton" for c in by_id.values())


def test_open_buttons_validate_and_use_verified_components() -> None:
    from qualify.a2ui.catalog import GE_RENDER_VERIFIED

    msgs = _view(links={"folder": "https://sp/f", "business": "https://sp/b"}, storage_label="SharePoint")
    validate_surface(msgs)
    used = {c["component"] for c in _components(msgs).values()}
    assert used <= GE_RENDER_VERIFIED, used - GE_RENDER_VERIFIED


def test_completed_intake_offers_the_technical_review() -> None:
    by_id = _components(_view(committed={0, 1, 2, 3}, complete=True))
    assert (START_TECH_REVIEW, {"prompt": "Start technical review", "recordId": "UC-2026-QW0001"}) in _buttons(by_id)


def test_dossier_preview_points_to_the_checklist_instead_of_the_matrix() -> None:
    by_id = _components(_view("tech"))
    bodies = [c["text"] for k, c in by_id.items() if k.startswith("ws-doc-dossier-s") and k.endswith("-b")]
    assert any("Checklist" in b for b in bodies)
    assert not any("| :--- |" in b for b in bodies)


def test_markdown_sections_strip_alerts_and_rules() -> None:
    md = "# Title\n> [!CAUTION]\n> **Blocked.**\n---\n## One\nbody\n## Two\n"
    assert markdown_sections(md) == [("Overview", "⛔ **Blocked.**"), ("One", "body")]


def test_open_workspace_button_is_added_to_stage_cards() -> None:
    from qualify.a2ui.compiler import build_surface

    msgs = add_open_workspace_button(build_surface(load_pack("business"), _portfolio()[0], 0, "s"), "UC-1")
    validate_surface(msgs)
    by_id = _components(msgs)
    assert by_id["root"]["children"][-1] == "ws-open"
    assert by_id["ws-open"]["action"]["event"]["name"] == OPEN_WORKSPACE


def test_workspace_uses_only_components_seen_rendering_in_ge() -> None:
    from qualify.a2ui.catalog import GE_RENDER_VERIFIED

    used = {c["component"] for c in _components(_view("tech")).values()}
    assert used <= GE_RENDER_VERIFIED, used - GE_RENDER_VERIFIED


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def test_panel_surface_id_keeps_the_stage_card_current() -> None:
    session = new_session("ctx-ws-a")
    card = session.next_surface_id()
    panel = session.panel_surface_id("workspace")
    assert panel != card and session.current_surface_id == card


def test_document_links_survive_serialisation() -> None:
    session = new_session("ctx-ws-b")
    session.document_links = {"folder": "https://x/f", "business": "https://x/b"}
    assert _session_from_dict(_session_to_dict(session)).document_links == session.document_links


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


def _start(store, ctx: str):
    return execute_turn(store, TurnInput(context_id=ctx, user_text="Start"))


def test_stage_cards_carry_an_open_workspace_button() -> None:
    store = InMemorySessionStore(quiet=True)
    out = _start(store, "ctx-ws-1")
    assert "ws-open" in _components(out.a2ui_messages)


def test_open_workspace_button_opens_the_panel_without_moving_patches() -> None:
    store = InMemorySessionStore(quiet=True)
    first = _start(store, "ctx-ws-2")
    card_id = first.session.current_surface_id
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-ws-2", action_data={"name": OPEN_WORKSPACE, "context": {}}),
    )
    validate_surface(out.a2ui_messages)
    assert _components(out.a2ui_messages)["root"]["component"] == "Canvas"
    assert "Opened the workspace" in out.reply_text
    assert out.session.current_surface_id == card_id


@pytest.mark.parametrize(
    "phrase,first_tab",
    [("open workspace", "Progress"), ("Show progress", "Progress"), ("my documents", "Documents")],
)
def test_typed_workspace_commands(phrase: str, first_tab: str) -> None:
    store = InMemorySessionStore(quiet=True)
    _start(store, "ctx-ws-3")
    out = execute_turn(store, TurnInput(context_id="ctx-ws-3", user_text=phrase))
    assert _components(out.a2ui_messages)["ws-tabs"]["tabs"][0]["title"] == first_tab


def test_longer_sentences_do_not_trigger_the_workspace() -> None:
    store = InMemorySessionStore(quiet=True)
    _start(store, "ctx-ws-4")
    out = execute_turn(
        store, TurnInput(context_id="ctx-ws-4", user_text="we track progress in documents by hand")
    )
    assert "Opened the workspace" not in out.reply_text


def test_workspace_command_is_off_with_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERACTIVE_VIEWS", "0")
    store = InMemorySessionStore(quiet=True)
    out = _start(store, "ctx-ws-5")
    assert "ws-open" not in _components(out.a2ui_messages)
    out = execute_turn(store, TurnInput(context_id="ctx-ws-5", user_text="open workspace"))
    assert "Opened the workspace" not in out.reply_text


def test_workspace_reflects_a_skipped_stage() -> None:
    store = InMemorySessionStore(quiet=True)
    ctx = "ctx-ws-6"
    _start(store, ctx)
    execute_turn(store, TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": NEEDS_PAYLOAD}))
    execute_turn(store, TurnInput(context_id=ctx, action_data={"name": SKIP_STAGE, "context": {"stage": "sizing"}}))
    out = execute_turn(store, TurnInput(context_id=ctx, user_text="workspace"))
    by_id = _components(out.a2ui_messages)
    assert by_id["ws-st-sizing"]["title"].startswith("⚠️")
    assert by_id["ws-st-data"]["title"].startswith("✏️")


def test_brief_panel_links_to_the_workspace() -> None:
    store = InMemorySessionStore(quiet=True)
    ctx = "ctx-ws-7"
    _start(store, ctx)
    out = None
    for payload in (NEEDS_PAYLOAD, SIZING_PAYLOAD, DATA_PAYLOAD, OWNERSHIP_PAYLOAD):
        out = execute_turn(store, TurnInput(context_id=ctx, action_data={"name": COMMIT_STAGE, "context": payload}))
    assert out is not None and "br-workspace" in _components(out.a2ui_messages)


def test_tech_review_start_opens_the_workspace_on_the_brief(sharepoint) -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-ws-8",
            action_data={"name": START_TECH_REVIEW, "context": {"recordId": "UC-2026-SB0001"}},
        ),
    )
    assert out.session.pack_name == "tech"
    canvases = [
        m for m in out.a2ui_messages
        if "updateComponents" in m
        and any(c.get("component") == "Canvas" for c in m["updateComponents"]["components"])
    ]
    assert len(canvases) == 1
    by_id = {c["id"]: c for c in canvases[0]["updateComponents"]["components"]}
    assert by_id["ws-tabs"]["tabs"][0]["title"] == "Documents"
    assert by_id["ws-doc-brief"]["expanded"] is True
    assert "side panel" in out.reply_text
    # The business intake's SharePoint folder and brief open from the panel.
    links = out.session.document_links
    assert "UC-2026-SB0001" in links["folder"]
    assert links["business"].startswith(links["folder"]) and links["business"].endswith(".md")
    assert by_id["ws-doc-folder"]["action"]["functionCall"]["args"]["url"] == links["folder"]
    assert "SharePoint" in by_id["ws-doc-brief-open"]["label"]
    # Live extraction patches must keep going to the stage card.
    assert out.session.current_surface_id.startswith("qualify-s0-")


# ---------------------------------------------------------------------------
# Live-refresh probe
# ---------------------------------------------------------------------------


def test_live_probe_validates_and_bump_is_data_only() -> None:
    validate_surface(build_live_probe())
    msgs = build_live_bump({"n": 2})
    assert len(msgs) == 1 and msgs[0]["updateDataModel"]["surfaceId"] == LIVE_SURFACE_ID
    assert msgs[0]["updateDataModel"]["value"]["count"] == "Refreshed 3 times"


def test_live_probe_turns() -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-ws-9", user_text="probe live"))
    assert any("createSurface" in m for m in out.a2ui_messages)
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-ws-9", action_data={"name": PROBE_LIVE_BUMP, "context": {"n": 0}}),
    )
    assert not any("createSurface" in m for m in out.a2ui_messages)
    assert "refreshes in place" in out.reply_text


def test_preview_markdown_keeps_a_readable_hierarchy() -> None:
    """Sub-headings must not outsize the h5 section title; quote lines stay apart."""
    from qualify.a2ui.views.workspace import _clean_markdown

    out = _clean_markdown(
        "> **Gate 1 Status:** ok  \n> **Record ID:** `UC-1`\n\n### User Stories\nAs an analyst…"
    )
    assert "**User Stories**" in out and "###" not in out
    assert "**Gate 1 Status:** ok\n\n**Record ID:** `UC-1`" in out


def test_brief_has_no_latex() -> None:
    """GE's markdown renderer shows $…$ math as raw text."""
    from qualify.export.brief import render_business_brief
    from qualify.schema.use_case_record import Meta, UseCaseRecord

    rec = UseCaseRecord(meta=Meta(record_id="UC-2026-TEX001", initiative_name="x"))
    rec.business.user_count = 10
    rec.sizing.task_frequency_weekly = 5
    rec.sizing.target_minutes_saved_per_task = 6
    md = render_business_brief(rec)
    assert "\\times" not in md and "\\text" not in md and "$U$" not in md


def test_tech_review_start_without_storage_has_no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from qualify.agent.handover import review_document_links

    monkeypatch.setenv("STORAGE_PROVIDER", "none")
    assert review_document_links("UC-2026-SB0001") == {}


def test_review_document_links_survives_a_failing_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    from qualify.agent.handover import review_document_links

    class Broken:
        def list_opportunities(self, *a, **k):
            raise RuntimeError("Graph down")

    monkeypatch.setattr("qualify.connectors.sharepoint.get_sharepoint_connector", lambda: Broken())
    assert review_document_links("UC-2026-SB0001") == {}
