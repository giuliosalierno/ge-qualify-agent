"""Tests for starting a technical review from SharePoint.

Two behaviours are covered here, and they failed in different ways before this
existed.

**The listing was dead code.** `turn.py` called `connector.search_opportunities`,
a method that has never existed on `SharePointConnector`. Typing
`list sharepoint` raised `AttributeError` into the executor's guard and showed
the user a generic failure. No test referenced the command, so 398 of them
stayed green over it. `test_the_listing_method_the_agent_calls_exists` is the
cheap guard against a repeat.

**The handover could only read the record store.** A record written by a
revision with no `QUALIFY_GCS_BUCKET` — which is every revision before
`00066-k6w` — exists in SharePoint and nowhere else, so a review of it could
never start.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from qualify.agent.handover import (
    HandoverError,
    list_pending_reviews,
    load_review_record,
    start_tech_review,
)
from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors.sharepoint import (
    SharePointConnector,
    split_opportunity_folder_name,
)
from qualify.export import deliverable_filename
from qualify.schema.use_case_record import Meta, UseCaseRecord
from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import InMemorySessionStore

BRIEF = deliverable_filename("business")
DOSSIER = deliverable_filename("tech")


@pytest.fixture(autouse=True)
def _force_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps every test in this file off the network.

    `conftest` already points `SHAREPOINT_MOCK_DIR` at a temp path, but a
    machine with real Graph credentials in the environment would promote the
    connector out of mock mode and try to reach SharePoint for real.
    """
    import qualify.connectors.sharepoint as sp_mod

    for var in (
        "MS_GRAPH_CLIENT_ID",
        "MS_GRAPH_CLIENT_SECRET",
        "MS_GRAPH_TENANT_ID",
        "MS_GRAPH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    sp_mod._TOKEN_VAULT.clear()
    sp_mod._CONNECTOR_INSTANCE = None


@pytest.fixture
def sharepoint_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The `Qualification Opportunities` folder inside a fresh mock drive."""
    root = tmp_path / "mock" / "drives" / "Documents" / "Qualification Opportunities"
    root.mkdir(parents=True)
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path / "mock"))
    return root


def _opportunity(
    root: Path,
    record_id: str,
    name: str,
    *,
    brief: bool = True,
    dossier: bool = False,
) -> Path:
    """Writes one opportunity folder exactly as `sync_opportunity` would."""
    folder = root / f"{record_id} - {name}"
    folder.mkdir(parents=True, exist_ok=True)
    record = UseCaseRecord(meta=Meta(record_id=record_id, initiative_name=name))
    (folder / "record.json").write_text(
        record.model_dump_json(indent=2), encoding="utf-8"
    )
    if brief:
        (folder / BRIEF).write_text(f"# {name}", encoding="utf-8")
    if dossier:
        (folder / DOSSIER).write_text(f"# {name} - technical", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# Folder naming
# ---------------------------------------------------------------------------


def test_the_listing_method_the_agent_calls_exists() -> None:
    """`turn.py` called `search_opportunities`, which never existed.

    Kept as its own test because the failure mode is invisible: the call sits
    behind a chat keyword, raises into a broad guard, and shows the user a
    generic error. Asserting the method name costs nothing and closes the only
    way this recurs.
    """
    assert hasattr(SharePointConnector, "list_opportunities")
    assert not hasattr(SharePointConnector, "search_opportunities")


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("UC-2026-A1B2C3 - Invoice Triage", ("UC-2026-A1B2C3", "Invoice Triage")),
        # An initiative whose own name contains the separator survives intact.
        (
            "UC-2026-A1B2C3 - Plan - Build - Run",
            ("UC-2026-A1B2C3", "Plan - Build - Run"),
        ),
        ("UC-2026-A1B2C3", ("UC-2026-A1B2C3", "")),
        ("uc-2026-abc - lowercase id", ("UC-2026-ABC", "lowercase id")),
        # Hand-made folders keep their name and yield no id, so they are
        # listable but never offered as a review target.
        ("Scratch notes", ("", "Scratch notes")),
    ],
)
def test_folder_names_split_into_id_and_initiative(
    folder: str, expected: tuple[str, str]
) -> None:
    assert split_opportunity_folder_name(folder) == expected


# ---------------------------------------------------------------------------
# The pending filter
# ---------------------------------------------------------------------------


def test_pending_includes_a_folder_with_only_a_brief(sharepoint_root: Path) -> None:
    _opportunity(sharepoint_root, "UC-2026-AAA111", "Invoice Triage")

    pending, reachable = list_pending_reviews()

    assert reachable
    assert [p["recordId"] for p in pending] == ["UC-2026-AAA111"]
    assert pending[0]["initiativeName"] == "Invoice Triage"


def test_pending_excludes_a_folder_that_already_holds_a_dossier(
    sharepoint_root: Path,
) -> None:
    _opportunity(sharepoint_root, "UC-2026-AAA111", "Already Reviewed", dossier=True)

    pending, reachable = list_pending_reviews()

    assert reachable
    assert pending == []


def test_pending_excludes_a_folder_with_no_brief(sharepoint_root: Path) -> None:
    """An abandoned folder is not work waiting to be done."""
    _opportunity(sharepoint_root, "UC-2026-AAA111", "Never Saved", brief=False)

    pending, _ = list_pending_reviews()

    assert pending == []


def test_pending_drops_folders_with_no_parsable_record_id(
    sharepoint_root: Path,
) -> None:
    """A folder we cannot name cannot be offered, even though it is listed."""
    stray = sharepoint_root / "Someone scratch folder"
    stray.mkdir()
    (stray / BRIEF).write_text("# stray", encoding="utf-8")

    pending, reachable = list_pending_reviews()

    assert reachable
    assert pending == []
    # It is still visible in the raw listing - dropped for review, not hidden.
    listed = SharePointConnector().list_opportunities()
    assert any(entry["name"] == "Someone scratch folder" for entry in listed)


def test_listing_reports_the_state_of_each_opportunity(
    sharepoint_root: Path,
) -> None:
    _opportunity(sharepoint_root, "UC-2026-AAA111", "Pending")
    _opportunity(sharepoint_root, "UC-2026-BBB222", "Done", dossier=True)

    by_id = {e["recordId"]: e for e in SharePointConnector().list_opportunities()}

    assert by_id["UC-2026-AAA111"]["hasBrief"] is True
    assert by_id["UC-2026-AAA111"]["hasDossier"] is False
    assert by_id["UC-2026-BBB222"]["hasDossier"] is True


# ---------------------------------------------------------------------------
# Where the record comes from
# ---------------------------------------------------------------------------


def test_handover_falls_back_to_sharepoint_when_the_store_misses(
    sharepoint_root: Path, tmp_path: Path
) -> None:
    """The case that made this feature necessary.

    Every revision before `00066-k6w` ran an in-memory store, so records from
    those conversations exist in SharePoint and nowhere else.
    """
    _opportunity(sharepoint_root, "UC-2026-SPONLY", "Only In SharePoint")
    store = LocalRecordStore(tmp_path / "store")

    session = start_tech_review(store, "ctx-reviewer", "UC-2026-SPONLY")

    assert session.pack_name == "tech"
    assert session.record.meta.initiative_name == "Only In SharePoint"


def test_handover_prefers_the_store_when_both_hold_the_record(
    sharepoint_root: Path, tmp_path: Path
) -> None:
    """The store is the fresher copy, so it wins.

    SharePoint's `record.json` is a snapshot from the last sync and can lag an
    interview that carried on afterwards.
    """
    _opportunity(sharepoint_root, "UC-2026-BOTH01", "Stale SharePoint Name")

    store = LocalRecordStore(tmp_path / "store")
    fresher = UseCaseRecord(
        meta=Meta(record_id="UC-2026-BOTH01", initiative_name="Fresher Store Name")
    )
    store.save_record(fresher)

    record = load_review_record(store, "UC-2026-BOTH01")

    assert record is not None
    assert record.meta.initiative_name == "Fresher Store Name"


def test_an_id_in_neither_place_is_refused_with_both_named(
    sharepoint_root: Path, tmp_path: Path
) -> None:
    store = LocalRecordStore(tmp_path / "store")

    with pytest.raises(HandoverError, match="UC-2026-NOWHERE") as exc:
        start_tech_review(store, "ctx", "UC-2026-NOWHERE")

    message = str(exc.value).lower()
    assert "record store" in message
    assert "sharepoint" in message


def test_an_in_memory_store_still_names_the_deployment_gap(
    sharepoint_root: Path,
) -> None:
    """The advice has to stay actionable now that there are two sources.

    Without a durable store the reviewer needs to hear about
    `QUALIFY_GCS_BUCKET`, not just that an id was not found.
    """
    with pytest.raises(HandoverError, match="QUALIFY_GCS_BUCKET"):
        start_tech_review(InMemorySessionStore(quiet=True), "ctx", "UC-2026-ABC123")


# ---------------------------------------------------------------------------
# The chat reply
# ---------------------------------------------------------------------------


def test_a_review_request_without_an_id_lists_what_is_pending(
    sharepoint_root: Path, tmp_path: Path
) -> None:
    _opportunity(sharepoint_root, "UC-2026-AAA111", "Invoice Triage")
    _opportunity(sharepoint_root, "UC-2026-BBB222", "Ticket Summarisation")
    _opportunity(sharepoint_root, "UC-2026-CCC333", "Already Done", dossier=True)
    store = LocalRecordStore(tmp_path / "store")

    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text="start a technical review"),
    )

    assert "Invoice Triage" in out.reply_text
    assert "UC-2026-AAA111" in out.reply_text
    assert "Ticket Summarisation" in out.reply_text
    # The reviewed one is not offered as work.
    assert "Already Done" not in out.reply_text
    # Nothing was started - the reviewer still has to choose.
    assert out.session.pack_name == "business"


def test_nothing_pending_says_so_and_still_accepts_an_id(
    sharepoint_root: Path, tmp_path: Path
) -> None:
    """An empty list is a real answer, and must not read as a failure."""
    _opportunity(sharepoint_root, "UC-2026-CCC333", "Already Done", dossier=True)
    store = LocalRecordStore(tmp_path / "store")

    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text="start a technical review"),
    )

    assert "nothing is waiting" in out.reply_text.lower()
    # The typed-id route stays visible for someone who already knows theirs.
    assert "record id" in out.reply_text.lower()
    assert out.session.pack_name == "business"


def test_an_unreachable_sharepoint_is_not_reported_as_no_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The distinction this whole return signature exists for.

    "Nothing to review" is a conclusion a reviewer acts on. Reporting it when
    the truth is "I could not look" sends them away from work that is waiting.
    """
    import qualify.connectors.sharepoint as sp_mod

    def _boom() -> None:
        raise RuntimeError("Graph is down")

    monkeypatch.setattr(sp_mod, "get_sharepoint_connector", _boom)
    store = LocalRecordStore(tmp_path / "store")

    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text="start a technical review"),
    )

    reply = out.reply_text.lower()
    assert "couldn't reach sharepoint" in reply
    assert "nothing is waiting" not in reply


def test_list_sharepoint_command_runs_without_erroring(
    sharepoint_root: Path, tmp_path: Path
) -> None:
    """The command that raised `AttributeError` from the day it was written."""
    _opportunity(sharepoint_root, "UC-2026-AAA111", "Invoice Triage")
    _opportunity(sharepoint_root, "UC-2026-BBB222", "Already Done", dossier=True)
    store = LocalRecordStore(tmp_path / "store")

    out = execute_turn(
        store, TurnInput(context_id="ctx-reviewer", user_text="list sharepoint")
    )

    assert "Invoice Triage" in out.reply_text
    assert "awaiting technical review" in out.reply_text
    assert "technically reviewed" in out.reply_text


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


def test_the_picker_survives_the_a2a_protocol_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Runs the request over real HTTP, not through `execute_turn`.

    Unit tests call the turn loop directly and therefore cannot see a rejection
    that happens upstream of it. That is precisely how every A2UI button in
    this project stayed broken while 283 tests passed, so anything on the
    protocol boundary needs a test that speaks HTTP.
    """
    root = tmp_path / "mock" / "drives" / "Documents" / "Qualification Opportunities"
    root.mkdir(parents=True)
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path / "mock"))
    _opportunity(root, "UC-2026-WIRE01", "Invoice Triage")

    import qualify.agent.server as server_mod

    def _no_llm(*_args, **_kwargs):
        raise RuntimeError("LLM disabled for tests")

    monkeypatch.setattr(server_mod, "GeminiExtractionClient", _no_llm)
    monkeypatch.setattr(server_mod, "GeminiChatClient", _no_llm)
    monkeypatch.setenv("SIGNIN_CARD", "0")
    monkeypatch.setenv("SHAREPOINT_MOCK", "1")
    monkeypatch.setenv("QUALIFY_DATA_DIR", str(tmp_path / "store"))

    app, _host, _port = server_mod.build_app()
    client = TestClient(app)

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/stream",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "contextId": "ctx-wire-picker",
                "parts": [{"kind": "text", "text": "technical review"}],
            }
        },
    }

    errors: list[dict] = []
    texts: list[str] = []
    with client.stream("POST", "/", json=payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            body = json.loads(line[5:])
            if "error" in body:
                errors.append(body["error"])
                continue
            status = (body.get("result", {}) or {}).get("status", {}) or {}
            for part in (status.get("message", {}) or {}).get("parts", []):
                if part.get("kind") == "text":
                    texts.append(part["text"])

    assert errors == [], f"protocol boundary rejected the turn: {errors}"
    assert any("UC-2026-WIRE01" in t for t in texts), texts


def test_graph_401_raises_rather_than_falling_back_to_empty_mock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reproduces the exact Cloud Run bug where Graph 401 fell back to empty mock.

    When real Graph credentials are configured (`auth_mode != 'mock'`), a Graph
    failure (`_list_graph_opportunities` -> `None`) must raise rather than
    silently falling back to `.data/sharepoint_mock` (which is empty in Cloud
    Run) and telling the user every opportunity already has a dossier.
    """
    connector = SharePointConnector()
    monkeypatch.setattr(
        connector,
        "get_graph_headers",
        lambda *_a, **_kw: ({"Authorization": "Bearer fake"}, "client_credentials"),
    )
    monkeypatch.setattr(
        connector, "_list_graph_opportunities", lambda *_a, **_kw: None
    )

    with pytest.raises(RuntimeError, match="SharePoint Graph opportunity listing failed"):
        connector.list_opportunities(pending_technical_review=True)


def test_unreachable_sharepoint_emits_signin_card_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When SharePoint cannot be reached, the reviewer gets the Microsoft sign-in button."""
    import qualify.connectors.sharepoint as sp_mod

    monkeypatch.setenv("SIGNIN_CARD", "1")
    monkeypatch.setattr(
        sp_mod,
        "get_sharepoint_connector",
        lambda: (_ for _ in ()).throw(RuntimeError("401 Unauthorized")),
    )
    store = LocalRecordStore(tmp_path / "store")

    out = execute_turn(
        store,
        TurnInput(context_id="ctx-reviewer", user_text="let's start a tech review"),
    )

    assert "couldn't reach sharepoint" in out.reply_text.lower()
    assert "nothing is waiting" not in out.reply_text.lower()
    assert len(out.a2ui_messages) > 0

