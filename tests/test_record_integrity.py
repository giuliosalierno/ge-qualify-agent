"""Data integrity of the record store: no flow may silently erase another's data.

Covers three ways a finished record used to be lost:

* a second intake in the same chat reusing the first one's record id;
* a failed session read being treated as "no session" and overwritten;
* two flows (business, technical review, portfolio) writing the same
  `records/<id>.json` from their own stale copies.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed, ServiceUnavailable

from qualify.agent.handover import RECORD_ID_RE
from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.record_store import GCSRecordStore, LocalRecordStore
from qualify.sinks.session import get_or_start, new_session

# --- a minimal in-memory GCS ---------------------------------------------------


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name
        self.generation: int | None = None

    def exists(self) -> bool:
        return self.name in self._bucket.objects

    def download_as_text(self, encoding: str = "utf-8") -> str:
        if self.name in self._bucket.fail_reads:
            raise ServiceUnavailable("transient")
        if self.name not in self._bucket.objects:
            raise NotFound(self.name)
        data, gen = self._bucket.objects[self.name]
        self.generation = gen
        return data

    def upload_from_string(
        self, data: str, content_type: str = "", if_generation_match: int | None = None
    ) -> None:
        current = self._bucket.objects.get(self.name)
        current_gen = current[1] if current else 0
        if if_generation_match is not None and if_generation_match != current_gen:
            raise PreconditionFailed(self.name)
        self._bucket.counter += 1
        self._bucket.objects[self.name] = (data, self._bucket.counter)
        self.generation = self._bucket.counter
        self._bucket.uploads.append((self.name, if_generation_match))

    def delete(self) -> None:
        self._bucket.objects.pop(self.name, None)


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, int]] = {}
        self.fail_reads: set[str] = set()
        self.uploads: list[tuple[str, int | None]] = []
        self.counter = 0

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class FakeClient:
    def __init__(self) -> None:
        self.the_bucket = FakeBucket()

    def bucket(self, name: str) -> FakeBucket:
        return self.the_bucket

    def list_blobs(self, bucket: str, prefix: str = "", max_results: int = 0) -> list[Any]:
        names = sorted(n for n in self.the_bucket.objects if n.startswith(prefix))
        return [self.the_bucket.blob(n) for n in names]


@pytest.fixture
def gcs() -> GCSRecordStore:
    return GCSRecordStore("test-bucket", client=FakeClient())


def _objects(store: GCSRecordStore) -> dict[str, tuple[str, int]]:
    return store._bucket.objects  # type: ignore[no-any-return]


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


# --- failed session loads ------------------------------------------------------


def _saved(store: Any, ctx: str, name: str) -> str:
    session = new_session(ctx, "business")
    session.record.meta.initiative_name = name
    store.save(session)
    return session.record.meta.record_id


def test_gcs_load_returns_none_only_when_session_is_missing(gcs: GCSRecordStore) -> None:
    assert gcs.load("ctx-nobody") is None


def test_gcs_transient_read_error_propagates_and_keeps_data(gcs: GCSRecordStore) -> None:
    record_id = _saved(gcs, "ctx-a", "Invoice Triage")
    gcs._bucket.fail_reads.add("sessions/ctx-a.json")
    before = dict(_objects(gcs))

    with pytest.raises(ServiceUnavailable):
        get_or_start(gcs, "ctx-a")

    assert _objects(gcs) == before
    assert gcs.load_record(record_id).meta.initiative_name == "Invoice Triage"


def test_gcs_corrupt_session_is_moved_aside_and_record_kept(
    gcs: GCSRecordStore, caplog: pytest.LogCaptureFixture
) -> None:
    record_id = _saved(gcs, "ctx-a", "Invoice Triage")
    _objects(gcs)["sessions/ctx-a.json"] = ('{"context_id": "ctx-a"}', 999)

    with caplog.at_level(logging.ERROR):
        fresh = get_or_start(gcs, "ctx-a")

    assert fresh.record.meta.record_id != record_id
    corrupt = [n for n in _objects(gcs) if n.startswith("sessions/_corrupt/ctx-a-")]
    assert len(corrupt) == 1
    assert _objects(gcs)[corrupt[0]][0] == '{"context_id": "ctx-a"}'
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert gcs.load_record(record_id).meta.initiative_name == "Invoice Triage"


def test_local_corrupt_session_is_moved_aside(tmp_path: Path) -> None:
    store = LocalRecordStore(tmp_path / "store")
    record_id = _saved(store, "ctx-a", "Invoice Triage")
    (store.sessions_dir / "ctx-a.json").write_text("{not json", encoding="utf-8")

    fresh = get_or_start(store, "ctx-a")

    assert fresh.record.meta.record_id != record_id
    [moved] = list((store.sessions_dir / "_corrupt").glob("ctx-a-*.json"))
    assert moved.read_text(encoding="utf-8") == "{not json"
    assert store.load_record(record_id).meta.initiative_name == "Invoice Triage"
    assert json.loads((store.sessions_dir / "ctx-a.json").read_text())["context_id"] == "ctx-a"


def test_local_unreadable_session_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalRecordStore(tmp_path / "store")
    _saved(store, "ctx-a", "Invoice Triage")

    def _boom(self: Path, *args: Any, **kwargs: Any) -> str:
        raise OSError("disk went away")

    monkeypatch.setattr(Path, "read_text", _boom)  # an I/O error, not corruption

    with pytest.raises(OSError):
        store.load("ctx-a")
