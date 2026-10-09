"""Provider-neutral storage layer: provider selection, vault sharing, queueing."""

from __future__ import annotations

from pathlib import Path

import pytest

import qualify.connectors.sharepoint as sp_mod
from qualify.connectors import storage, token_vault
from qualify.connectors.sharepoint import SharePointConnector
from qualify.schema.use_case_record import Meta, UseCaseRecord


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    token_vault.ACCESS.clear()
    token_vault.REFRESH.clear()
    token_vault.PENDING.clear()
    sp_mod._CONNECTOR_INSTANCE = None
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    monkeypatch.delenv("SHAREPOINT_MOCK", raising=False)
    yield
    token_vault.ACCESS.clear()
    token_vault.REFRESH.clear()
    token_vault.PENDING.clear()
    sp_mod._CONNECTOR_INSTANCE = None


def _record(name: str = "Invoice Triage") -> UseCaseRecord:
    return UseCaseRecord(meta=Meta(record_id="UC-2026-ABC123", initiative_name=name))


def test_default_provider_is_sharepoint() -> None:
    assert storage.storage_provider() == "sharepoint"
    assert isinstance(storage.get_storage_connector(), SharePointConnector)


def test_unknown_provider_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "dropbox")
    with pytest.raises(ValueError, match="Unsupported STORAGE_PROVIDER"):
        storage.storage_provider()
    # The chat-facing helper must swallow it rather than break the turn.
    assert storage.sync_to_storage(_record(), context_id="ctx") is None


def test_sharepoint_names_alias_the_shared_objects() -> None:
    """Existing tests and callers rely on the historical names still working."""
    assert sp_mod._TOKEN_VAULT is token_vault.ACCESS
    assert sp_mod._REFRESH_VAULT is token_vault.REFRESH
    assert sp_mod._PENDING_RECORDS is token_vault.PENDING
    assert sp_mod.SharePointAuthRequired is storage.StorageAuthRequired
    assert sp_mod.SharePointSyncResult is storage.StorageSyncResult


def test_sharepoint_connector_satisfies_protocol() -> None:
    assert isinstance(SharePointConnector(), storage.StorageConnector)


def test_is_connected_reads_only_this_conversation() -> None:
    token_vault.put_access("ctx-a", "tok-a", 600)
    assert storage.is_connected("ctx-a")
    assert not storage.is_connected("ctx-b")
    assert not storage.is_connected(None)


def test_expired_token_is_evicted() -> None:
    token_vault.put_access("ctx-a", "tok-a", -1)
    assert token_vault.get_access("ctx-a") is None
    assert "ctx-a" not in token_vault.ACCESS


def test_save_tokens_requires_context() -> None:
    with pytest.raises(ValueError):
        token_vault.save_tokens("", refresh_token="rt")


def test_unauthenticated_sync_queues_with_pack_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending tech dossier must not be written later as a business brief."""
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", "tenant")
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", "client")
    monkeypatch.delenv("SHAREPOINT_APP_AUTH", raising=False)

    res = storage.sync_to_storage(_record(), context_id="ctx-1", pack_name="tech")

    assert res is not None and res.auth_mode == "unauthenticated"
    queued = token_vault.PENDING["ctx-1"]
    assert queued[2] == "tech"


def test_auto_sync_uses_queued_pack_name(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    class _Recorder(SharePointConnector):
        def sync_opportunity(self, record, **kwargs):  # type: ignore[override]
            seen["pack_name"] = kwargs["pack_name"]
            seen["token"] = kwargs["delegated_token"]
            return storage.StorageSyncResult(
                success=True, record_id="r", folder_url="", brief_url="", auth_mode="delegated"
            )

    token_vault.queue_pending("ctx-1", _record(), {3}, "tech")
    res = storage.auto_sync_pending("tok", "ctx-1", connector=_Recorder(mock_dir=tmp_path))

    assert res is not None and res.auth_mode == "delegated"
    assert seen == {"pack_name": "tech", "token": "tok"}
    assert "ctx-1" not in token_vault.PENDING


def test_delegated_sync_clears_queue(tmp_path: Path) -> None:
    class _Ok(SharePointConnector):
        def sync_opportunity(self, record, **kwargs):  # type: ignore[override]
            return storage.StorageSyncResult(
                success=True, record_id="r", folder_url="", brief_url="", auth_mode="delegated"
            )

    token_vault.queue_pending("ctx-1", _record(), set(), "business")
    storage.sync_record(_Ok(mock_dir=tmp_path), _record(), context_id="ctx-1")
    assert "ctx-1" not in token_vault.PENDING


def test_connector_errors_never_escape(tmp_path: Path) -> None:
    class _Boom(SharePointConnector):
        def sync_opportunity(self, record, **kwargs):  # type: ignore[override]
            raise RuntimeError("graph down")

    assert storage.sync_record(_Boom(mock_dir=tmp_path), _record(), context_id="c") is None
