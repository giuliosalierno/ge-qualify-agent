"""The go/demos Click-to-Deploy profile: no document storage, no fixed URLs.

Every C2D deployment lands in a fresh Argolis project. These tests pin the two
properties that make that work:

- nothing in the shipped code points at one particular deployment, and
- ``STORAGE_PROVIDER=none`` gives a complete experience from the record store,
  never a Microsoft sign-in prompt the tester cannot complete.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qualify.agent.handover import list_pending_reviews, load_review_record
from qualify.agent.turn import TurnInput, execute_turn
from qualify.config import agent_base_url
from qualify.connectors import storage
from qualify.schema.use_case_record import Meta, UseCaseRecord
from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import Session

REPO = Path(__file__).resolve().parent.parent

#: Identifiers of the maintainer's own deployment. None may ship in code.
_DEPLOYMENT_SPECIFIC = re.compile(
    r"g22bhpwccq|vais-c-exp|369594916120|\d+\.\d+\.\d+\.\d+\.nip\.io|"
    r"gemini-enterprise-1788|53514d2f-4bc9"
)


def test_no_deployment_specific_identifiers_in_shipped_code() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for path in (REPO / "qualify").rglob("*.py")
        if _DEPLOYMENT_SPECIFIC.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# agent_base_url
# ---------------------------------------------------------------------------


def test_agent_url_wins_and_loses_its_trailing_slash(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_URL", "https://example.run.app/")
    assert agent_base_url() == "https://example.run.app"


def test_without_agent_url_links_point_at_localhost(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_URL", raising=False)
    monkeypatch.setenv("PORT", "9000")
    assert agent_base_url() == "http://localhost:9000"


# ---------------------------------------------------------------------------
# STORAGE_PROVIDER=none
# ---------------------------------------------------------------------------


@pytest.fixture
def no_storage(monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "none")


@pytest.fixture
def store(tmp_path: Path) -> LocalRecordStore:
    return LocalRecordStore(tmp_path / "records")


def _finished(store: LocalRecordStore, record_id: str, name: str, pack: str = "business") -> None:
    """Writes a finished session exactly as the agent would at the last stage."""
    session = Session(
        context_id=f"ctx-{record_id}-{pack}",
        pack_name=pack,
        record=UseCaseRecord(meta=Meta(record_id=record_id, initiative_name=name)),
    )
    session.committed = set(range(len(session.pack.stages)))
    assert session.is_complete
    store.save(session)


def test_none_is_a_supported_provider(no_storage) -> None:
    assert storage.storage_enabled() is False
    with pytest.raises(storage.StorageDisabled):
        storage.get_storage_connector()
    assert storage.is_connected("any") is False
    rec = UseCaseRecord(meta=Meta(record_id="UC-2026-DEMO01"))
    assert storage.sync_to_storage(rec, context_id="any") is None


def test_default_provider_is_unchanged() -> None:
    assert storage.storage_enabled() is True


def test_pending_reviews_come_from_the_record_store(no_storage, store) -> None:
    _finished(store, "UC-2026-DEMO01", "Claims triage")
    _finished(store, "UC-2026-DEMO02", "Invoice matching")
    _finished(store, "UC-2026-DEMO02", "Invoice matching", pack="tech")

    pending, reachable = list_pending_reviews(store=store)

    assert reachable is True
    assert [e["recordId"] for e in pending] == ["UC-2026-DEMO01"]


def test_an_empty_record_store_means_nothing_pending(no_storage, store) -> None:
    assert list_pending_reviews(store=store) == ([], True)


def test_handover_reads_the_record_store_only(no_storage, store) -> None:
    _finished(store, "UC-2026-DEMO01", "Claims triage")
    assert load_review_record(store, "UC-2026-DEMO01").meta.initiative_name == "Claims triage"
    assert load_review_record(store, "UC-2026-NOPE00") is None


def _say(store, text: str, context_id: str = "demo-ctx"):
    return execute_turn(store, TurnInput(context_id=context_id, user_text=text))


def test_save_to_sharepoint_explains_instead_of_asking_for_sign_in(no_storage, store) -> None:
    out = _say(store, "save to sharepoint")
    assert "isn't connected" in out.reply_text
    assert "Sign in" not in out.reply_text
    assert not out.auth_required


def test_portfolio_review_scores_the_record_store(no_storage, store) -> None:
    _finished(store, "UC-2026-DEMO01", "Claims triage")
    out = _say(store, "portfolio review")
    assert "Claims triage" in out.reply_text
    assert "SharePoint" not in out.reply_text


def test_empty_portfolio_has_no_sign_in_card(no_storage, store) -> None:
    out = _say(store, "portfolio review")
    assert "No qualified opportunities yet" in out.reply_text
    assert out.a2ui_messages == []


def test_help_does_not_mention_sharepoint(no_storage, store) -> None:
    out = _say(store, "help")
    assert "SharePoint" not in out.reply_text
