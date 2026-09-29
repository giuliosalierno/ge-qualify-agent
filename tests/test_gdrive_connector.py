"""Google Drive connector: live code path against an in-memory fake Drive."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

import qualify.connectors.gdrive as gd
from qualify.connectors import storage, token_vault
from qualify.connectors.gdrive import GoogleDriveConnector, _q_literal
from qualify.connectors.storage import StorageAuthRequired
from qualify.schema.use_case_record import Meta, UseCaseRecord


class FakeDrive:
    """Just enough of Drive v3 to exercise the connector: files, q, uploads."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.content: dict[str, bytes] = {}
        self.auth_headers: list[str] = []
        self.queries: list[str] = []
        self.fail_status: int | None = None
        self._next = 0

    def _new_id(self) -> str:
        self._next += 1
        return f"id{self._next}"

    def _add(self, name: str, parent: str, mime: str) -> dict[str, Any]:
        fid = self._new_id()
        meta = {
            "id": fid,
            "name": name,
            "mimeType": mime,
            "parents": [parent],
            "webViewLink": f"https://drive.google.com/{fid}",
            "modifiedTime": "2026-09-29T00:00:00Z",
        }
        self.files[fid] = meta
        return meta

    def _match(self, q: str) -> list[dict[str, Any]]:
        def lit(pattern: str) -> str | None:
            m = re.search(pattern, q)
            if not m:
                return None
            return m.group(1).replace("\\'", "'").replace("\\\\", "\\")

        name = lit(r"name = '((?:[^'\\]|\\.)*)'")
        parent = lit(r"'((?:[^'\\]|\\.)*)' in parents")
        out = []
        for f in self.files.values():
            if name is not None and f["name"] != name:
                continue
            if parent is not None and parent not in f["parents"]:
                continue
            is_folder = f["mimeType"] == gd.FOLDER_MIME
            if f"mimeType = '{gd.FOLDER_MIME}'" in q and not is_folder:
                continue
            if f"mimeType != '{gd.FOLDER_MIME}'" in q and is_folder:
                continue
            out.append(f)
        return sorted(out, key=lambda f: f["name"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth_headers.append(request.headers.get("authorization", ""))
        if self.fail_status:
            return httpx.Response(self.fail_status, json={"error": {"code": self.fail_status}})
        path = request.url.path
        params = dict(request.url.params)

        if request.method == "GET" and path == "/drive/v3/files":
            self.queries.append(params["q"])
            return httpx.Response(200, json={"files": self._match(params["q"])})
        if request.method == "GET" and path.startswith("/drive/v3/files/"):
            fid = path.rsplit("/", 1)[1]
            return httpx.Response(200, content=self.content[fid])
        if request.method == "POST" and path == "/drive/v3/files":
            body = json.loads(request.content)
            return httpx.Response(200, json=self._add(body["name"], body["parents"][0], body["mimeType"]))
        if request.method == "POST" and path == "/upload/drive/v3/files":
            boundary = request.headers["content-type"].split("boundary=")[1]
            parts = request.content.split(f"--{boundary}".encode())
            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
            mime_line, payload = parts[2].split(b"\r\n\r\n", 1)
            mime = mime_line.decode().split("Content-Type: ")[1].strip()
            created = self._add(meta["name"], meta["parents"][0], mime)
            self.content[created["id"]] = payload[: -len(b"\r\n")]
            return httpx.Response(200, json=created)
        if request.method == "PATCH" and path.startswith("/upload/drive/v3/files/"):
            fid = path.rsplit("/", 1)[1]
            self.content[fid] = request.content
            return httpx.Response(200, json=self.files[fid])
        return httpx.Response(404)


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    fake = FakeDrive()
    real_client = httpx.Client

    def _client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gd.httpx, "Client", _client)
    return fake


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    token_vault.ACCESS.clear()
    token_vault.REFRESH.clear()
    token_vault.PENDING.clear()
    gd._CONNECTOR_INSTANCE = None
    monkeypatch.delenv("GDRIVE_MOCK", raising=False)
    yield
    token_vault.ACCESS.clear()
    token_vault.REFRESH.clear()
    token_vault.PENDING.clear()
    gd._CONNECTOR_INSTANCE = None


def _live() -> GoogleDriveConnector:
    return GoogleDriveConnector(client_id="cid", client_secret="csecret")


def _record(rid: str = "UC-2026-ABC123", name: str = "Invoice Triage") -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id=rid, initiative_name=name))


# --- auth contract ---------------------------------------------------------


def test_unauthenticated_without_token_and_nothing_written(drive: FakeDrive) -> None:
    res = _live().sync_opportunity(_record(), context_id="ctx-1")
    assert res.auth_mode == "unauthenticated" and not res.success
    assert drive.files == {}


def test_uses_only_this_conversations_token(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-a", "tok-a", 600)
    token_vault.put_access("ctx-b", "tok-b", 600)
    _live().sync_opportunity(_record(), context_id="ctx-a")
    assert drive.auth_headers and set(drive.auth_headers) == {"Bearer tok-a"}


def test_reads_require_sign_in(drive: FakeDrive) -> None:
    with pytest.raises(StorageAuthRequired):
        _live().list_opportunities(context_id="ctx-1")
    with pytest.raises(StorageAuthRequired):
        _live().load_opportunity("UC-2026-ABC123", context_id="ctx-1")


def test_401_clears_the_vault_and_asks_to_sign_in_again(drive: FakeDrive) -> None:
    token_vault.save_tokens("ctx-1", refresh_token="rt", access_token="dead", expires_in=3600)
    drive.fail_status = 401
    res = _live().sync_opportunity(_record(), context_id="ctx-1")
    assert res.auth_mode == "unauthenticated"
    assert not token_vault.has_session("ctx-1")


def test_server_error_never_claims_success(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    drive.fail_status = 500
    res = _live().sync_opportunity(_record(), context_id="ctx-1")
    assert res.auth_mode == "error" and not res.success and res.folder_url == ""


def test_refresh_token_is_exchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    drive = FakeDrive()
    token_vault.save_tokens("ctx-1", refresh_token="rt-1")
    real_client = httpx.Client
    posted: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            posted.update(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        return drive.handler(request)

    monkeypatch.setattr(
        gd.httpx, "Client",
        lambda *a, **k: real_client(*a, **{**k, "transport": httpx.MockTransport(handler)}),
    )
    headers, mode = _live().get_headers(context_id="ctx-1")
    assert mode == "delegated" and headers["Authorization"] == "Bearer fresh"
    assert posted["grant_type"] == "refresh_token" and posted["refresh_token"] == "rt-1"
    # Google omits refresh_token on refresh; the original must survive.
    assert token_vault.get_refresh("ctx-1") == "rt-1"


# --- layout and round trip -------------------------------------------------


def test_sync_creates_layout_and_round_trips(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    conn = _live()
    res = conn.sync_opportunity(_record(), context_id="ctx-1")
    assert res.success and res.auth_mode == "delegated"
    assert res.folder_url.startswith("https://drive.google.com/")

    names = {f["name"] for f in drive.files.values()}
    assert {"Qualification Opportunities", "UC-2026-ABC123 - Invoice Triage",
            "Business_Value_Brief.md", "record.json"} <= names

    loaded = conn.load_opportunity("uc-2026-abc123", context_id="ctx-1")
    assert loaded is not None and loaded.meta.initiative_name == "Invoice Triage"


def test_resync_overwrites_instead_of_duplicating(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    conn = _live()
    conn.sync_opportunity(_record(), context_id="ctx-1")
    count = len(drive.files)
    conn.sync_opportunity(_record(), context_id="ctx-1")
    assert len(drive.files) == count


def test_tech_dossier_lands_next_to_brief_and_pending_filter(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    conn = _live()
    conn.sync_opportunity(_record("UC-2026-AAA111", "Alpha"), context_id="ctx-1")
    conn.sync_opportunity(_record("UC-2026-BBB222", "Beta"), context_id="ctx-1")
    conn.sync_opportunity(_record("UC-2026-BBB222", "Beta"), context_id="ctx-1", pack_name="tech")

    listing = conn.list_opportunities(context_id="ctx-1")
    assert [e["recordId"] for e in listing] == ["UC-2026-AAA111", "UC-2026-BBB222"]
    pending = conn.list_opportunities(context_id="ctx-1", pending_technical_review=True)
    assert [e["recordId"] for e in pending] == ["UC-2026-AAA111"]

    everything = conn.load_all_opportunities(context_id="ctx-1")
    assert {r.meta.record_id for r, _ in everything} == {"UC-2026-AAA111", "UC-2026-BBB222"}


def test_listing_before_first_save_is_empty(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    assert _live().list_opportunities(context_id="ctx-1") == []


def test_portfolio_report_upload(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    url = _live().sync_portfolio_report("# Report", context_id="ctx-1")
    assert url and url.startswith("https://drive.google.com/")


# --- input safety ----------------------------------------------------------


def test_query_literal_escapes_quotes_and_backslashes() -> None:
    assert _q_literal("O'Brien") == "'O\\'Brien'"
    assert _q_literal("a\\b") == "'a\\\\b'"


def test_hostile_initiative_name_cannot_rewrite_the_query(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    evil = "x' or name contains '"
    res = _live().sync_opportunity(_record(name=evil), context_id="ctx-1")
    assert res.success
    for q in drive.queries:
        # Every quote coming from the name must arrive escaped.
        assert "x' or" not in q


def test_oversized_upload_is_rejected(drive: FakeDrive) -> None:
    token_vault.put_access("ctx-1", "tok", 600)
    conn = _live()
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="upload limit"):
            conn._upload(client, {}, "root", "big.md", b"x" * (gd.MAX_UPLOAD_BYTES + 1), "text/plain")


# --- mock mode and provider selection --------------------------------------


def test_mock_mode_when_client_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GDRIVE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setenv("GDRIVE_MOCK_DIR", str(tmp_path))
    conn = GoogleDriveConnector()
    res = conn.sync_opportunity(_record(), context_id="ctx-1")
    assert res.auth_mode == "mock"
    assert (tmp_path / "Qualification Opportunities" / "UC-2026-ABC123 - Invoice Triage" / "record.json").is_file()
    assert conn.list_opportunities()[0]["hasBrief"]
    assert conn.load_opportunity("Invoice") is not None


def test_storage_provider_gdrive_selects_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "gdrive")
    conn = storage.get_storage_connector()
    assert isinstance(conn, GoogleDriveConnector)
    assert isinstance(conn, storage.StorageConnector)
    assert conn.display_name == "Google Drive"
