"""Record-store fallbacks for users who cannot sign in to SharePoint.

go/demo testers have no account in the SharePoint tenant. Portfolio review and
the "awaiting technical review" list must still work for them, from finished
records only — never from half-completed drafts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import qualify.connectors.sharepoint as sp_mod
from qualify.agent.handover import list_pending_reviews
from qualify.agent.turn import TurnInput, execute_turn
from qualify.connectors import token_vault
from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import Session, new_session


@pytest.fixture(autouse=True)
def _sharepoint_configured_but_signed_out(monkeypatch: pytest.MonkeyPatch):
    """The go/demo situation: SharePoint is set up, this user has no session."""
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", "tenant")
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", "client")
    monkeypatch.delenv("SHAREPOINT_MOCK", raising=False)
    monkeypatch.delenv("SHAREPOINT_APP_AUTH", raising=False)
    token_vault.ACCESS.clear()
    token_vault.REFRESH.clear()
    sp_mod._CONNECTOR_INSTANCE = None
    yield
    sp_mod._CONNECTOR_INSTANCE = None


@pytest.fixture
def store(tmp_path: Path) -> LocalRecordStore:
    return LocalRecordStore(tmp_path / "store")


def _finished(ctx: str, name: str, pack: str = "business") -> Session:
    session = new_session(ctx, pack)
    session.record.meta.initiative_name = name
    session.committed = set(range(len(session.pack.stages)))
    return session


# --- completion index -------------------------------------------------------


def test_drafts_are_not_listed(store: LocalRecordStore) -> None:
    draft = new_session("ctx-draft", "business")
    draft.record.meta.initiative_name = "Half Done"
    draft.committed = {0}
    store.save(draft)
    assert store.list_completed() == []


def test_finished_intake_is_listed_awaiting_review(store: LocalRecordStore) -> None:
    session = _finished("ctx-1", "Invoice Triage")
    store.save(session)
    [entry] = store.list_completed()
    assert entry["recordId"] == session.record.meta.record_id
    assert entry["hasBrief"] is True and entry["hasDossier"] is False
    assert entry["initiativeName"] == "Invoice Triage"


def test_finished_tech_review_marks_dossier_on_same_record(store: LocalRecordStore) -> None:
    business = _finished("ctx-1", "Invoice Triage")
    store.save(business)
    tech = _finished("ctx-2", "Invoice Triage", pack="tech")
    tech.record = business.record
    store.save(tech)
    [entry] = store.list_completed()
    assert entry["hasBrief"] is True and entry["hasDossier"] is True


def test_load_portfolio_items_pairs_records(store: LocalRecordStore) -> None:
    store.save(_finished("ctx-1", "Alpha"))
    store.save(_finished("ctx-2", "Beta"))
    items = store.load_portfolio_items()
    assert {rec.meta.initiative_name for rec, _ in items} == {"Alpha", "Beta"}


# --- pending technical reviews ---------------------------------------------


def test_pending_list_falls_back_to_record_store(store: LocalRecordStore) -> None:
    alpha = _finished("ctx-1", "Alpha")
    store.save(alpha)
    beta = _finished("ctx-2", "Beta")
    store.save(beta)
    reviewed = _finished("ctx-3", "Beta", pack="tech")
    reviewed.record = beta.record
    store.save(reviewed)

    pending, reachable = list_pending_reviews("ctx-reviewer", store=store)
    assert reachable
    assert [e["recordId"] for e in pending] == [alpha.record.meta.record_id]


def test_pending_list_without_store_still_reports_unreachable() -> None:
    assert list_pending_reviews("ctx-reviewer") == ([], False)


# --- portfolio review --------------------------------------------------------


def test_portfolio_uses_record_store_when_sharepoint_unavailable(store: LocalRecordStore) -> None:
    store.save(_finished("ctx-1", "Invoice Triage"))
    out = execute_turn(store, TurnInput(context_id="ctx-coe", user_text="portfolio review"))
    assert "saved copies" in out.reply_text
    assert "Invoice Triage" in out.reply_text
    # Data is still shown, but the user is told they are signed out and how to fix it.
    assert "not signed in" in out.reply_text and "sign in with Microsoft" in out.reply_text
    assert out.a2ui_messages == []  # views are off here: markdown only, no card


def test_portfolio_still_offers_sign_in_when_nothing_is_finished(store: LocalRecordStore) -> None:
    out = execute_turn(store, TurnInput(context_id="ctx-coe", user_text="portfolio review"))
    assert "sign in with Microsoft" in out.reply_text
