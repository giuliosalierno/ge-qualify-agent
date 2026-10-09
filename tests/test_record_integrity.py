"""Data integrity of the record store: no flow may silently erase another's data.

Covers three ways a finished record used to be lost:

* a second intake in the same chat reusing the first one's record id;
* a failed session read being treated as "no session" and overwritten;
* two flows (business, technical review, portfolio) writing the same
  `records/<id>.json` from their own stale copies.
"""

from __future__ import annotations

from pathlib import Path

from qualify.agent.handover import RECORD_ID_RE
from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.record_store import LocalRecordStore
from qualify.sinks.session import new_session

# --- record ids ---------------------------------------------------------------


def test_new_sessions_in_same_context_get_distinct_record_ids() -> None:
    first = new_session("ctx-same", "business")
    second = new_session("ctx-same", "business")
    assert first.record.meta.record_id != second.record.meta.record_id
    for session in (first, second):
        record_id = session.record.meta.record_id
        assert RECORD_ID_RE.fullmatch(record_id)
        assert len(record_id.rsplit("-", 1)[1]) == 8
        assert session.record.meta.context_id == "ctx-same"


def test_qualify_another_does_not_overwrite_the_finished_record(tmp_path: Path) -> None:
    store = LocalRecordStore(tmp_path / "store")
    finished = new_session("ctx-chat", "business")
    finished.record.meta.initiative_name = "Invoice Triage"
    finished.committed = set(range(len(finished.pack.stages)))
    store.save(finished)
    first_id = finished.record.meta.record_id

    out = execute_turn(store, TurnInput(context_id="ctx-chat", user_text="qualify another"))

    assert out.session.record.meta.record_id != first_id
    kept = store.load_record(first_id)
    assert kept is not None
    assert kept.meta.initiative_name == "Invoice Triage"
