"""SharePoint sign-in that ends mid-conversation: restart, rejected token, 401.

Tokens live only in process memory (by design), so a deploy signs everyone
out, and Entra can reject a token early. The user must be told once and offered
the sign-in again, never left with silent failures.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import qualify.connectors.sharepoint as sp_mod
from qualify.a2ui.signin import SIGNIN_SURFACE_ID
from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors import token_vault
from qualify.connectors.sharepoint import SharePointConnector
from qualify.schema.use_case_record import Meta, UseCaseRecord
from qualify.sinks.session import InMemorySessionStore, get_or_start

_TENANT = "00000000-0000-0000-0000-000000000001"
_CLIENT = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _entra(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("MS_GRAPH_CLIENT_SECRET", "SHAREPOINT_MOCK", "SHAREPOINT_APP_AUTH", "STORAGE_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", _TENANT)
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("AGENT_URL", "https://agent.example.run.app")
    monkeypatch.setenv("OAUTH_STATE_SECRET", "test-secret")
    token_vault.ACCESS.clear()
    token_vault.REFRESH.clear()
    token_vault.PENDING.clear()
    sp_mod._CONNECTOR_INSTANCE = None


def _signin_surfaces(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("createSurface", {}).get("surfaceId") == SIGNIN_SURFACE_ID)


def _confirmed_session(store: InMemorySessionStore, ctx: str) -> None:
    """A conversation that was connected earlier (the flag is persisted)."""
    session = get_or_start(store, ctx)
    session.signin_confirmed = True
    session.signin_prompted = True
    store.save(session)


# ---------------------------------------------------------------------------
# 401 hook
# ---------------------------------------------------------------------------


def _response(status: int, token: str) -> httpx.Response:
    req = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me", headers={"Authorization": f"Bearer {token}"})
    return httpx.Response(status, request=req)


def test_a_401_signs_only_that_conversation_out() -> None:
    token_vault.save_tokens("ctx-a", refresh_token="rt-a", access_token="tok-a")
    token_vault.save_tokens("ctx-b", refresh_token="rt-b", access_token="tok-b")
    token_vault.forget_rejected_token(_response(401, "tok-a"))
    assert not token_vault.has_session("ctx-a")  # refresh token dropped too
    assert token_vault.has_session("ctx-b")


@pytest.mark.parametrize("status", [200, 403, 404, 500])
def test_other_statuses_keep_the_token(status: int) -> None:
    token_vault.save_tokens("ctx-a", refresh_token="rt-a", access_token="tok-a")
    token_vault.forget_rejected_token(_response(status, "tok-a"))
    assert token_vault.get_access("ctx-a") == "tok-a"


def test_a_401_mid_save_asks_for_sign_in_instead_of_a_local_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    token_vault.save_tokens("ctx-s", refresh_token="rt", access_token="tok-s")
    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(lambda r: httpx.Response(401, request=r))}),
    )
    rec = UseCaseRecord(meta=Meta(record_id="UC-2026-EXP001", initiative_name="Expiry"))
    res = SharePointConnector().sync_opportunity(rec, context_id="ctx-s")
    assert res.success is False and res.auth_mode == "unauthenticated"
    assert not token_vault.has_session("ctx-s")


# ---------------------------------------------------------------------------
# Telling the user
# ---------------------------------------------------------------------------


def test_lost_sign_in_is_announced_once_with_the_sign_in_card() -> None:
    store = InMemorySessionStore(quiet=True)
    _confirmed_session(store, "ctx-lost")  # vault is empty: a restart happened

    out = execute_turn(store, TurnInput(context_id="ctx-lost", user_text="help"))
    assert "session has expired" in out.reply_text
    assert _signin_surfaces(out.a2ui_messages) == 1
    assert out.session.signin_confirmed is False

    again = execute_turn(store, TurnInput(context_id="ctx-lost", user_text="help"))
    assert "session has expired" not in again.reply_text
    assert _signin_surfaces(again.a2ui_messages) == 0


def test_a_refresh_token_alone_still_counts_as_connected() -> None:
    store = InMemorySessionStore(quiet=True)
    _confirmed_session(store, "ctx-rt")
    token_vault.save_tokens("ctx-rt", refresh_token="rt-only")  # access token expired
    out = execute_turn(store, TurnInput(context_id="ctx-rt", user_text="help"))
    assert "session has expired" not in out.reply_text


def test_never_signed_in_gets_no_expiry_notice() -> None:
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(store, TurnInput(context_id="ctx-new", user_text="help"))
    assert "session has expired" not in out.reply_text


def test_reconnecting_shows_the_connected_banner_again() -> None:
    store = InMemorySessionStore(quiet=True)
    _confirmed_session(store, "ctx-re")
    execute_turn(store, TurnInput(context_id="ctx-re", user_text="help"))  # expiry notice
    token_vault.save_tokens("ctx-re", refresh_token="rt", access_token="tok")  # signed in again
    out = execute_turn(store, TurnInput(context_id="ctx-re", user_text="help"))
    assert "connected" in out.reply_text.lower()


def test_no_duplicate_card_when_the_reply_already_asks_to_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The portfolio already offers sign-in when signed out; add the notice, not a second card."""
    from qualify.connectors.storage import StorageAuthRequired
    from qualify.sinks.record_store import LocalRecordStore
    from tests.test_views import _portfolio

    class SignedOut:
        display_name = "Microsoft SharePoint"
        account_label = "Microsoft"

        def load_all_opportunities(self, *a, **k):
            raise StorageAuthRequired("sign in")

    monkeypatch.setattr(sp_mod, "get_sharepoint_connector", lambda: SignedOut())
    import tempfile

    store = LocalRecordStore(tempfile.mkdtemp())
    from qualify.sinks.session import Session

    for r in _portfolio()[:1]:
        s = Session(context_id=f"ctx-{r.meta.record_id}", pack_name="business", record=r)
        s.committed = set(range(len(s.pack.stages)))
        store.save(s)
    _confirmed_session(store, "ctx-pf")

    out = execute_turn(store, TurnInput(context_id="ctx-pf", user_text="portfolio review"))
    assert out.reply_text.count("session has expired") == 1
    assert _signin_surfaces(out.a2ui_messages) == 0
