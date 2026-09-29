"""Security tests for SharePoint token handling.

Each test pins one property that the previous design violated in production:

- tokens were shared across conversations through a `"latest"` key,
- `/token` handed live user tokens to unauthenticated callers,
- the conversation bound to a sign-in came from an unsigned query parameter,
- the callback page displayed the refresh token,
- missing user auth silently fell back to app-only or mock access.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

import qualify.connectors.sharepoint as sp_mod
from qualify.connectors import oauth_state
from qualify.connectors.sharepoint import (
    SharePointAuthRequired,
    SharePointConnector,
    cache_delegated_token,
    get_cached_delegated_token,
    has_user_session,
    save_delegated_refresh_token,
    sync_to_optional_sharepoint,
)
from qualify.mcp.sharepoint_mcp import (
    handle_oauth_auth,
    handle_oauth_callback,
    handle_oauth_status,
)
from qualify.schema.use_case_record import Meta, UseCaseRecord

_TENANT = "00000000-0000-0000-0000-000000000001"
_CLIENT = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("MS_GRAPH_CLIENT_SECRET", "MS_GRAPH_REFRESH_TOKEN", "SHAREPOINT_MOCK", "SHAREPOINT_APP_AUTH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", _TENANT)
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("AGENT_URL", "https://agent.example.run.app")
    monkeypatch.setenv("OAUTH_STATE_SECRET", "test-secret")
    sp_mod._TOKEN_VAULT.clear()
    sp_mod._REFRESH_VAULT.clear()
    sp_mod._APP_TOKEN.clear()
    sp_mod._PENDING_RECORDS.clear()
    sp_mod._CONNECTOR_INSTANCE = None


def _request(path: str, query: str = "") -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode("utf-8"),
            "headers": [(b"host", b"agent.example.run.app")],
            "server": ("agent.example.run.app", 443),
            "scheme": "https",
        }
    )


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _FakeClient:
    """Stands in for httpx.Client; records posts and replies with a canned response."""

    posts: list[dict[str, Any]] = []
    reply = _FakeResp(200, {"access_token": "eyJ_user_access", "refresh_token": "rt_user", "expires_in": 3600})

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def post(self, url: str, data: dict[str, Any] | None = None, **_kw: Any) -> _FakeResp:
        _FakeClient.posts.append({"url": url, "data": data or {}})
        return _FakeClient.reply


# ---------------------------------------------------------------------------
# Conversation isolation
# ---------------------------------------------------------------------------


def test_a_token_is_never_visible_to_another_conversation() -> None:
    save_delegated_refresh_token("rt_alice", "eyJ_alice", 3600, context_id="ctx-alice")

    assert get_cached_delegated_token("ctx-alice") == "eyJ_alice"
    assert get_cached_delegated_token("ctx-bob") is None
    assert get_cached_delegated_token("latest") is None
    assert not has_user_session("ctx-bob")

    headers, mode = SharePointConnector().get_graph_headers(context_id="ctx-bob")
    assert mode == "unauthenticated"
    assert "Authorization" not in headers


def test_tokens_are_never_written_to_disk_or_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.chdir(tmp_path)
    save_delegated_refresh_token("rt_secret", "eyJ_secret", 3600, context_id="ctx-1")

    assert "MS_GRAPH_REFRESH_TOKEN" not in os.environ
    assert not any(tmp_path.rglob("*.json"))


def test_storing_tokens_requires_a_conversation() -> None:
    with pytest.raises(ValueError):
        save_delegated_refresh_token("rt", "eyJ_x", 3600, context_id="")
    cache_delegated_token("eyJ_x", key="")
    assert not sp_mod._TOKEN_VAULT


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_no_silent_app_only_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MS_GRAPH_CLIENT_SECRET", "s3cret")
    monkeypatch.setattr(sp_mod.httpx, "Client", _FakeClient)
    _FakeClient.posts = []

    _, mode = SharePointConnector().get_graph_headers(context_id="ctx-none")

    assert mode == "unauthenticated"
    assert _FakeClient.posts == [], "no client_credentials request may be made implicitly"


def test_app_only_access_is_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MS_GRAPH_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("SHAREPOINT_APP_AUTH", "1")
    monkeypatch.setattr(sp_mod.httpx, "Client", _FakeClient)
    _FakeClient.reply = _FakeResp(200, {"access_token": "eyJ_app", "expires_in": 3600})

    headers, mode = SharePointConnector().get_graph_headers(context_id="ctx-none")

    assert mode == "client_credentials"
    assert headers["Authorization"] == "Bearer eyJ_app"
    _FakeClient.reply = _FakeResp(200, {"access_token": "eyJ_user_access", "refresh_token": "rt_user", "expires_in": 3600})


def test_unauthenticated_save_writes_nothing_and_queues_for_that_conversation() -> None:
    record = UseCaseRecord(meta=Meta(record_id="UC-2026-ABC123", initiative_name="Demo"))

    res = sync_to_optional_sharepoint(record, context_id="ctx-1")

    assert res is not None and res.success is False
    assert res.auth_mode == "unauthenticated"
    assert set(sp_mod._PENDING_RECORDS) == {"ctx-1"}


def test_unauthenticated_listing_asks_for_sign_in() -> None:
    with pytest.raises(SharePointAuthRequired):
        SharePointConnector().list_opportunities(context_id="ctx-1")


def test_expired_refresh_token_signs_the_user_out(monkeypatch: pytest.MonkeyPatch) -> None:
    sp_mod._REFRESH_VAULT["ctx-1"] = "rt_revoked"
    monkeypatch.setattr(sp_mod.httpx, "Client", _FakeClient)
    _FakeClient.reply = _FakeResp(400, {"error": "invalid_grant"})

    _, mode = SharePointConnector().get_graph_headers(context_id="ctx-1")

    assert mode == "unauthenticated"
    assert not has_user_session("ctx-1")
    _FakeClient.reply = _FakeResp(200, {"access_token": "eyJ_user_access", "refresh_token": "rt_user", "expires_in": 3600})


# ---------------------------------------------------------------------------
# Signed links and state
# ---------------------------------------------------------------------------


def test_signed_link_round_trip_and_tamper_detection() -> None:
    token = oauth_state.issue("ctx-1", "link", 600)
    assert oauth_state.verify(token, "link") == "ctx-1"

    body, sig = token.split(".")
    assert oauth_state.verify(f"{body}.{sig[:-2]}AA", "link") is None
    assert oauth_state.verify(token, "state") is None, "a link must not be replayable as OAuth state"
    assert oauth_state.verify(oauth_state.issue("ctx-1", "link", -1), "link") is None


def test_signin_url_carries_no_raw_context_id() -> None:
    url = oauth_state.build_signin_url("https://agent.example.run.app/", "ctx-secret-123")
    assert url.startswith("https://agent.example.run.app/auth?t=")
    assert "context_id=" not in url


def test_auth_page_rejects_unsigned_context_id() -> None:
    response = asyncio.run(handle_oauth_auth(_request("/auth", "context_id=ctx-victim")))
    assert response.status_code == 400


def test_auth_status_requires_signed_link() -> None:
    save_delegated_refresh_token("rt", "eyJ_x", 3600, context_id="ctx-1")

    forged = asyncio.run(handle_oauth_status(_request("/auth/status", "context_id=ctx-1")))
    assert forged.status_code == 400

    t = oauth_state.issue("ctx-1", "link", 600)
    ok = asyncio.run(handle_oauth_status(_request("/auth/status", f"t={t}")))
    assert json.loads(ok.body)["authenticated"] is True


def test_callback_rejects_forged_state() -> None:
    import base64

    forged_state = base64.urlsafe_b64encode(json.dumps({"context_id": "ctx-victim"}).encode()).decode()
    response = asyncio.run(handle_oauth_callback(_request("/auth/callback", f"code=abc&state={forged_state}")))

    assert response.status_code == 400
    assert not has_user_session("ctx-victim")


def test_callback_binds_tokens_to_the_signed_conversation_and_never_renders_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify.mcp.sharepoint_mcp as mcp_mod

    monkeypatch.setattr(mcp_mod.httpx, "Client", _FakeClient)
    state = oauth_state.issue("ctx-1", "state", 600)

    response = asyncio.run(handle_oauth_callback(_request("/auth/callback", f"code=abc&state={state}")))
    page = response.body.decode("utf-8")

    assert response.status_code == 200
    assert get_cached_delegated_token("ctx-1") == "eyJ_user_access"
    assert sp_mod._REFRESH_VAULT == {"ctx-1": "rt_user"}
    assert "rt_user" not in page and "eyJ_user_access" not in page
    assert response.headers["cache-control"] == "no-store"


def test_callback_error_page_escapes_reflected_input() -> None:
    response = asyncio.run(
        handle_oauth_callback(_request("/auth/callback", "error=%3Cscript%3Ealert(1)%3C%2Fscript%3E"))
    )
    page = response.body.decode("utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# ---------------------------------------------------------------------------
# Removed surface
# ---------------------------------------------------------------------------


def test_token_and_exchange_endpoints_are_gone() -> None:
    from starlette.testclient import TestClient

    from qualify.agent.server import build_app

    app, _, _ = build_app()
    client = TestClient(app)
    assert client.get("/token").status_code in (404, 405)
    assert client.post("/token").status_code in (404, 405)
    assert client.post("/auth/exchange", json={"code_or_url": "x", "context_id": "ctx"}).status_code in (404, 405)


def test_agent_card_no_longer_advertises_token_endpoint() -> None:
    from qualify.agent.card import build_agent_card

    card = build_agent_card("https://agent.example.run.app").model_dump(exclude_none=True)
    assert not card.get("security_schemes")
    assert "/token" not in json.dumps(card)
