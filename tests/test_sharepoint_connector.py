"""Unit and integration tests for the Dual-Channel SharePoint Connector Layer (MCP + A2A)."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import zipfile

import pytest
from starlette.requests import Request

from qualify.a2ui.actions import COMMIT_STAGE
from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors.sharepoint import (
    SharePointConnector,
    cache_delegated_token,
    get_cached_delegated_token,
    sanitize_path_segment,
)
from qualify.mcp.sharepoint_mcp import handle_mcp_request, handle_oauth_auth, handle_oauth_token
from qualify.schema.use_case_record import Meta, UseCaseRecord
from qualify.sinks.session import InMemorySessionStore


#: Anything here silently promotes the connector out of mock mode.
#:
#: Real values leak in easily — `.env` sourced into the shell, or a prior
#: `deploy.sh` run — and the failure is baffling when it happens, because the
#: test looks pure. Clearing them keeps the suite honest regardless of who runs
#: it and on what machine.
_GRAPH_ENV_VARS = (
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CLIENT_SECRET",
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_REFRESH_TOKEN",
)


@pytest.fixture(autouse=True)
def _reset_sharepoint_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify.connectors.sharepoint as sp_mod

    for var in _GRAPH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    sp_mod._TOKEN_VAULT.clear()
    sp_mod._CONNECTOR_INSTANCE = None


def _make_docx_bytes(text: str, malicious_member: str | None = None) -> bytes:
    """Creates an in-memory .docx ZIP archive containing word/document.xml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>"
            "</w:document>"
        )
        zf.writestr("word/document.xml", xml.encode("utf-8"))
        if malicious_member:
            zf.writestr(malicious_member, b"traversal payload")
    return buf.getvalue()


def test_sanitize_path_segment_prevents_traversal() -> None:
    """Verifies that path traversal sequences and forbidden SharePoint characters are stripped."""
    assert sanitize_path_segment("../../etc/passwd") == "passwd"
    assert sanitize_path_segment("UC-2026-001: AP*Invoice?Brief") == "UC-2026-001_ AP_Invoice_Brief"
    assert sanitize_path_segment("   ...   ") == "unnamed"


def test_dual_layer_oauth_header_resolution(tmp_path: Path) -> None:
    """Verifies Layer 1 (delegated token caching) and Layer 3 (mock fallback)."""
    connector = SharePointConnector(mock_dir=tmp_path)

    # Without credentials or delegated token -> mock mode
    headers, mode = connector.get_graph_headers()
    assert mode == "mock"
    assert headers["Authorization"] == "Bearer mock_graph_token"

    # With explicit delegated user token -> delegated mode + cached in vault
    headers_del, mode_del = connector.get_graph_headers(delegated_token="eyJ_real_user_token_123")
    assert mode_del == "delegated"
    assert headers_del["Authorization"] == "Bearer eyJ_real_user_token_123"
    assert get_cached_delegated_token("latest") == "eyJ_real_user_token_123"


def test_sync_and_load_opportunity_folder_and_list(tmp_path: Path) -> None:
    """Verifies that sync_opportunity writes both the SharePoint folder files and List item, and load_opportunity restores it."""
    connector = SharePointConnector(mock_dir=tmp_path)

    rec = UseCaseRecord(meta=Meta(record_id="UC-2026-778899", initiative_name="Global Contract Reviewer"))
    rec.meta.department_bu = "Legal"
    rec.business.user_count = 40
    rec.sizing.task_frequency_weekly = 5
    rec.sizing.baseline_minutes_per_task = 60
    rec.sizing.target_minutes_saved_per_task = 30
    rec.proposed.business_owner = "Elena Rostova"
    rec.proposed.executive_sponsor = "VP Legal"

    # 1. Sync opportunity to SharePoint
    res = connector.sync_opportunity(rec)
    assert res.success
    assert res.record_id == "UC-2026-778899"
    assert "Business_Value_Brief.md" in res.brief_url

    # Verify folder files exist on disk
    folder_dir = (
        tmp_path
        / "drives"
        / "Documents"
        / "Qualification Opportunities"
        / "UC-2026-778899 - Global Contract Reviewer"
    )
    assert (folder_dir / "Business_Value_Brief.md").is_file()
    assert (folder_dir / "record.json").is_file()

    # Verify SharePoint List item exists in items.json
    list_file = tmp_path / "lists" / "AI Use Case Inventory" / "items.json"
    assert list_file.is_file()
    items = json.loads(list_file.read_text(encoding="utf-8"))
    assert len(items) == 1
    assert items[0]["fields"]["RecordId"] == "UC-2026-778899"
    assert items[0]["fields"]["BusinessOwner"] == "Elena Rostova"
    assert items[0]["fields"]["AnnualHoursSaved"] == 5000.0

    # 2. List opportunities
    listed = connector.list_opportunities(query="Contract")
    assert len(listed) == 1
    assert "UC-2026-778899" in listed[0]["name"]

    # 3. Load opportunity by Record ID
    loaded = connector.load_opportunity("UC-2026-778899")
    assert loaded is not None
    assert loaded.meta.initiative_name == "Global Contract Reviewer"
    assert loaded.proposed.business_owner == "Elena Rostova"
    assert loaded.derived.total_annual_team_hours_saved == 5000.0


def test_safe_docx_text_extraction() -> None:
    """Verifies safe .docx text extraction and protection against zip traversal."""
    docx_data = _make_docx_bytes(
        "Project Charter: Automate Supplier Onboarding across 150 buyers.",
        malicious_member="../../evil.txt",
    )
    extracted = SharePointConnector.extract_text_from_bytes(docx_data, filename="Charter.docx")
    assert "Automate Supplier Onboarding across 150 buyers." in extracted
    assert "traversal payload" not in extracted


def test_mcp_jsonrpc_server_and_delegated_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies the /mcp JSON-RPC endpoint (initialize, tools/list, tools/call) and Bearer token caching."""
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path))

    # Seed an opportunity
    connector = SharePointConnector(mock_dir=tmp_path)
    rec = UseCaseRecord(meta=Meta(record_id="UC-2026-554433", initiative_name="HR Policy Bot"))
    connector.sync_opportunity(rec)

    async def _call_mcp(body: dict, auth_header: str | None = None) -> dict:
        headers = [(b"content-type", b"application/json")]
        if auth_header:
            headers.append((b"authorization", auth_header.encode("utf-8")))

        async def receive():
            return {"type": "http.request", "body": json.dumps(body).encode("utf-8")}

        req = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": headers,
            },
            receive=receive,
        )
        resp = await handle_mcp_request(req)
        return json.loads(resp.body.decode("utf-8"))

    # 1. Initialize
    init_res = asyncio.run(_call_mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    assert init_res["result"]["serverInfo"]["name"] == "ge-qualify-sharepoint-mcp"

    # 2. tools/list
    list_res = asyncio.run(_call_mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    tool_names = {t["name"] for t in list_res["result"]["tools"]}
    assert "search_qualification_opportunities" in tool_names
    assert "load_qualification_opportunity" in tool_names
    assert "query_sharepoint_sites_lookup" in tool_names

    # 3. tools/call with delegated user Bearer token
    call_res = asyncio.run(
        _call_mcp(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "load_qualification_opportunity",
                    "arguments": {"recordIdOrName": "UC-2026-554433"},
                },
            },
            auth_header="Bearer eyJ_delegated_from_ge_mcp",
        )
    )
    content_text = call_res["result"]["content"][0]["text"]
    assert "HR Policy Bot" in content_text
    # Verify token was cached from the MCP call
    assert get_cached_delegated_token("latest") == "eyJ_delegated_from_ge_mcp"


def test_turn_loop_load_from_sharepoint_chat_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that typing 'load UC-2026-XXXXXX from sharepoint' loads the record into the A2UI session."""
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path))
    # Reset singleton so it uses tmp_path
    import qualify.connectors.sharepoint as sp_mod

    sp_mod._CONNECTOR_INSTANCE = SharePointConnector(mock_dir=tmp_path)

    rec = UseCaseRecord(meta=Meta(record_id="UC-2026-991122", initiative_name="Treasury Cash Forecasting"))
    rec.business.problem_description = "Manual spreadsheet consolidation across 12 banks."
    sp_mod._CONNECTOR_INSTANCE.sync_opportunity(rec)

    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(context_id="ctx-sp-chat", user_text="Please load UC-2026-991122 from SharePoint"),
    )
    assert "Loaded opportunity **Treasury Cash Forecasting** (`UC-2026-991122`) from SharePoint" in out.reply_text
    assert out.session.record.meta.initiative_name == "Treasury Cash Forecasting"
    assert len(out.a2ui_messages) == 3
    assert out.a2ui_messages[0]["createSurface"]["surfaceId"].startswith("qualify-complete-")


def test_turn_loop_save_to_sharepoint_and_post_login_auto_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that typing 'logged in save it into sharepoint' saves the session record and auto_sync_pending_records works."""
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path))
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    sp_mod._CONNECTOR_INSTANCE = SharePointConnector(mock_dir=tmp_path)
    sp_mod._TOKEN_VAULT.clear()
    sp_mod._REFRESH_VAULT.clear()
    sp_mod._PENDING_RECORDS.clear()
    sp_mod._SYNCED_RESULTS.clear()

    store = InMemorySessionStore(quiet=True)
    # 1. Start session and populate initiative
    session = get_or_start(store, "ctx-save-sp")
    session.record.meta.record_id = "UC-2026-445566"
    session.record.meta.initiative_name = "Automated Invoice Matching"
    store.save(session)

    # 2. User types 'logged in save it into sharepoint' BEFORE authenticating -> prompts login & queues pending record
    out1 = execute_turn(
        store,
        TurnInput(context_id="ctx-save-sp", user_text="logged in save it into sharepoint"),
    )
    assert "SharePoint User Login Required" in out1.reply_text or "Sign-In Required" in out1.reply_text
    assert "ctx-save-sp" in sp_mod._PENDING_RECORDS

    # 3. Simulate Microsoft sign-in completion by caching a delegated user token
    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-save-sp")

    # 4. User types 'logged in save it into sharepoint' AFTER authenticating -> saves with delegated auth mode
    out2 = execute_turn(
        store,
        TurnInput(context_id="ctx-save-sp", user_text="logged in save it into sharepoint"),
    )
    assert "Successfully Saved to SharePoint" in out2.reply_text
    assert "Automated Invoice Matching" in out2.reply_text


def _fake_ms_jwt() -> str:
    """Builds a syntactically valid Microsoft Entra JWT (header.payload.signature)."""
    import base64 as _b64

    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return _b64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    header = seg({"typ": "JWT", "alg": "RS256"})
    payload = seg({"iss": "https://sts.windows.net/918002ad/", "aud": "https://graph.microsoft.com"})
    return f"{header}.{payload}.signature_placeholder"


def test_harvest_microsoft_tokens_from_all_injection_shapes() -> None:
    """Verifies Gemini Enterprise tokens are captured from raw headers, JSON, and base64 payloads."""
    import base64 as _b64

    import qualify.connectors.sharepoint as sp_mod
    from qualify.connectors.sharepoint import get_cached_delegated_token, harvest_microsoft_tokens

    token = _fake_ms_jwt()

    # 1. Raw Bearer header, alongside a Google OIDC token that must be ignored
    sp_mod._TOKEN_VAULT.clear()
    google_jwt_payload = _b64.urlsafe_b64encode(
        json.dumps({"iss": "https://accounts.google.com", "aud": "ge-qualify"}).encode()
    ).decode().rstrip("=")
    headers = {
        "authorization": f"Bearer hdr.{google_jwt_payload}.sig",
        "x-serialized-auth-tokens": f"Bearer {token}",
    }
    assert harvest_microsoft_tokens(headers, "ctx-a") == token
    assert get_cached_delegated_token("ctx-a") == token

    # 2. JSON-encoded header value (GE serialized auth tokens map)
    sp_mod._TOKEN_VAULT.clear()
    json_header = {"x-serialized-auth-tokens": json.dumps({"sharepoint-auth": {"access_token": token}})}
    assert harvest_microsoft_tokens(json_header, "ctx-b") == token
    assert get_cached_delegated_token("ctx-b") == token

    # 3. Base64-encoded JSON header value
    sp_mod._TOKEN_VAULT.clear()
    b64_val = _b64.urlsafe_b64encode(json.dumps({"sharepoint-auth": token}).encode()).decode()
    assert harvest_microsoft_tokens({"x-serialized-auth-tokens": b64_val}, "ctx-c") == token
    assert get_cached_delegated_token("ctx-c") == token

    # 4. A2A message metadata (temp:<authorization-id> convention)
    sp_mod._TOKEN_VAULT.clear()
    metadata = {"temp:sharepoint-auth": {"accessToken": token}}
    assert harvest_microsoft_tokens(metadata, "ctx-d") == token
    assert get_cached_delegated_token("ctx-d") == token

    # 5. No token present returns None
    sp_mod._TOKEN_VAULT.clear()
    assert harvest_microsoft_tokens({"content-type": "application/json"}, "ctx-e") is None


def test_harvest_rejects_dotted_non_jwt_header_values() -> None:
    """Regression: the Cloud Run hostname must never be vaulted as a Graph token.

    The `host` header value `ge-qualify-agent-g22bhpwccq-uc.a.run.app` has three
    dot-separated segments, so it matched the JWT-shaped regex and was cached as
    the user's delegated token. Every subsequent Graph call then returned 401 and
    the connector silently fell back to the local mock.
    """
    import qualify.connectors.sharepoint as sp_mod
    from qualify.connectors.sharepoint import (
        get_cached_delegated_token,
        harvest_microsoft_tokens,
        is_strict_microsoft_jwt,
    )

    hostname = "ge-qualify-agent-g22bhpwccq-uc.a.run.app"
    assert is_strict_microsoft_jwt(hostname) is False

    sp_mod._TOKEN_VAULT.clear()
    real_headers = {
        "host": hostname,
        "user-agent": "Google-Discovery-Engine/1.0",
        "content-type": "application/json",
        "x-cloud-trace-context": "abc123/456;o=1",
        "traceparent": "00-abc.def.ghi-0000-01",
    }
    assert harvest_microsoft_tokens(real_headers, "ctx-host") is None
    assert get_cached_delegated_token("ctx-host") is None

    # A genuine Microsoft JWT in the same header set is still harvested.
    token = _fake_ms_jwt()
    assert harvest_microsoft_tokens({**real_headers, "x-serialized-auth-tokens": token}, "ctx-host2") == token


def test_turn_emits_auth_required_when_sharepoint_login_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies the turn loop flags auth_required so the executor emits A2A TaskState.auth_required."""
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path))
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    sp_mod._CONNECTOR_INSTANCE = SharePointConnector(mock_dir=tmp_path)
    sp_mod._TOKEN_VAULT.clear()
    sp_mod._REFRESH_VAULT.clear()
    sp_mod._PENDING_RECORDS.clear()
    sp_mod._SYNCED_RESULTS.clear()

    store = InMemorySessionStore(quiet=True)
    session = get_or_start(store, "ctx-auth-required")
    session.record.meta.record_id = "UC-2026-778800"
    session.record.meta.initiative_name = "Supplier Onboarding Bot"
    store.save(session)

    out = execute_turn(
        store,
        TurnInput(context_id="ctx-auth-required", user_text="save to sharepoint"),
    )
    assert out.auth_required is True
    assert "Sign-In Required" in out.reply_text


