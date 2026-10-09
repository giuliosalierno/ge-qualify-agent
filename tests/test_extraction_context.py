"""Extraction sees recent turns, but evidence must still be the user's words."""

from __future__ import annotations

from typing import Any

from qualify.a2ui.patcher import extract_drafts
from qualify.agent.turn import TurnInput, execute_turn
from qualify.packs.loader import load_pack
from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import InMemorySessionStore


class _Recorder:
    """Extraction stub: records what it was shown, proposes nothing."""

    def __init__(self) -> None:
        self.conversations: list[str] = []

    def propose(self, *, instruction: str, schema: dict, conversation: str) -> list[Any]:
        self.conversations.append(conversation)
        return []


class _QuotesTheAssistant:
    def propose(self, **_kwargs) -> list[Any]:
        return [
            {
                "path": "/uc/business/user_count",
                "value": "30",
                "evidence": "roughly how many of them are there",
            }
        ]


def test_evidence_quoting_the_assistant_is_rejected() -> None:
    pack = load_pack("business")
    conversation = (
        "Assistant: Who does this work, and roughly how many of them are there?\n\n"
        "User: yes, 30 of them"
    )
    result = extract_drafts(
        pack.stages[0],
        pack,
        conversation,
        _QuotesTheAssistant(),
        evidence_text="yes, 30 of them",
    )
    assert result.drafts == []


def test_second_message_reaches_the_extractor_with_the_previous_question() -> None:
    store = InMemorySessionStore(quiet=True)
    rec = _Recorder()
    ctx = "ctx-history-1"
    execute_turn(
        store,
        TurnInput(context_id=ctx, user_text="Our AP clerks re-key supplier invoices into SAP"),
        extraction_client=rec,
    )
    execute_turn(store, TurnInput(context_id=ctx, user_text="about 30"), extraction_client=rec)

    last = rec.conversations[-1]
    assert "User: Our AP clerks re-key supplier invoices into SAP" in last
    assert "Assistant:" in last
    assert last.rstrip().endswith("User: about 30")


def test_history_is_bounded_and_survives_a_reload(tmp_path) -> None:
    store = LocalRecordStore(str(tmp_path))
    ctx = "ctx-history-2"
    for i in range(6):
        execute_turn(store, TurnInput(context_id=ctx, user_text=f"message number {i} about invoices"))

    session = store.load(ctx)
    assert session is not None
    assert len(session.recent_turns) == 6
    assert session.recent_turns[-1]["role"] == "assistant"
    assert any("message number 5" in t["text"] for t in session.recent_turns)
    assert not any("message number 0" in t["text"] for t in session.recent_turns)


class _OneItemPerSource:
    def propose(self, **_kwargs) -> list[Any]:
        return [
            {"path": "/uc/technical/data_sources", "value": "sap", "evidence": "SAP"},
            {
                "path": "/uc/technical/data_sources",
                "value": "workspace_mail",
                "evidence": "the shared Outlook mailbox",
            },
        ]


def test_multi_select_items_are_merged_not_dropped() -> None:
    """Run 3: 'workspace_mail' was dropped as a duplicate of 'sap'."""
    pack = load_pack("business")
    data_stage = next(s for s in pack.stages if s.id == "data")
    text = "The data lives in SAP and the shared Outlook mailbox."
    result = extract_drafts(data_stage, pack, text, _OneItemPerSource())
    assert [d.value for d in result.drafts] == [["sap", "workspace_mail"]]
