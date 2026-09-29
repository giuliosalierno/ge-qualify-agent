"""Durable persistence for UseCaseRecords and active Sessions (Phase 3 / L13).

Two primary implementations:
1. `LocalRecordStore`: Writes JSON files under a local directory (`records/<id>.json` and `sessions/<context_id>.json`). Zero cloud dependencies, ideal for local development and unit tests.
2. `GCSRecordStore`: Writes JSON blobs to Google Cloud Storage (`gs://<bucket>/records/<id>.json` and `gs://<bucket>/sessions/<context_id>.json`). Works purely on GCP IAM without requiring Google Workspace licenses, survives Cloud Run container restarts, and enables Agent 2 (`ge-review-tech`) to load any qualified record by its `UC-2026-XXXXXX` ID.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from qualify.schema.use_case_record import UseCaseRecord
from qualify.sinks.session import Session, SessionStore

log = logging.getLogger(__name__)


@runtime_checkable
class RecordStore(Protocol):
    """Protocol for persisting and retrieving UseCaseRecord instances by record_id.

    Runtime-checkable because the handover has to ask a store, at run time,
    whether it can reach records written by an earlier conversation. The
    in-memory fallback cannot, and the difference decides whether a technical
    review can start at all.
    """

    def save_record(self, record: UseCaseRecord) -> str: ...

    def load_record(self, record_id: str) -> UseCaseRecord | None: ...


def _session_to_dict(session: Session) -> dict[str, Any]:
    return {
        "context_id": session.context_id,
        "pack_name": session.pack_name,
        "record": session.record.model_dump(mode="json"),
        "active_stage": session.active_stage,
        "committed": sorted(session.committed),
        "skipped": sorted(session.skipped),
        "rendered_stages": sorted(session.rendered_stages),
        "surface_seq": session.surface_seq,
        "current_surface_id": session.current_surface_id,
        "stage_surface_ids": {str(k): v for k, v in session.stage_surface_ids.items()},
        "signin_prompted": session.signin_prompted,
        "signin_dismissed": session.signin_dismissed,
        "signin_confirmed": session.signin_confirmed,
        "welcome_shown": session.welcome_shown,
        "pending_review_choices": session.pending_review_choices,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


def _session_from_dict(data: dict[str, Any]) -> Session:
    return Session(
        context_id=data["context_id"],
        pack_name=data.get("pack_name", "business"),
        record=UseCaseRecord.model_validate(data["record"]),
        active_stage=int(data.get("active_stage", 0)),
        committed=set(data.get("committed", [])),
        skipped=set(data.get("skipped", [])),
        rendered_stages=set(data.get("rendered_stages", [])),
        surface_seq=int(data.get("surface_seq", 0)),
        current_surface_id=data.get("current_surface_id", "qualify"),
        stage_surface_ids={
            int(k): str(v) for k, v in data.get("stage_surface_ids", {}).items()
        },
        signin_prompted=bool(data.get("signin_prompted", False)),
        signin_dismissed=bool(data.get("signin_dismissed", False)),
        signin_confirmed=bool(data.get("signin_confirmed", False)),
        welcome_shown=bool(data.get("welcome_shown", False)),
        pending_review_choices=list(data.get("pending_review_choices", [])),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


# ---------------------------------------------------------------------------
# Portfolio index: which records are *finished*, not just started
# ---------------------------------------------------------------------------
#
# `save()` writes the record on every turn, so `records/` also holds drafts
# abandoned half way. The portfolio and the "awaiting technical review" list
# must only show finished work, the same set SharePoint holds, because that is
# where a record lands only on completion or an explicit save.
#
# One small marker per record (`portfolio/<id>.json`) rather than a single
# index file, so two conversations finishing at once cannot overwrite each
# other's entry. Markers are only ever set, never cleared: reopening a stage
# of a finished brief does not un-qualify it.
#
# Visibility: like the shared SharePoint site, this index is shared by every
# user of the deployment. That is intended for the CoE portfolio view.


def _completion_entry(session: Session, existing: dict[str, Any] | None) -> dict[str, Any]:
    entry = dict(existing or {})
    record = session.record
    entry["recordId"] = record.meta.record_id
    entry["initiativeName"] = record.meta.initiative_name or entry.get("initiativeName") or ""
    entry["name"] = f"{record.meta.record_id} - {entry['initiativeName']}".rstrip(" -")
    if session.pack_name == "tech":
        entry["hasDossier"] = True
        entry.setdefault("hasBrief", True)
    else:
        entry["hasBrief"] = True
        entry.setdefault("hasDossier", False)
    entry["webUrl"] = None
    entry["lastModifiedDateTime"] = datetime.now().astimezone().isoformat()
    entry["source"] = "record_store"
    return entry


class LocalRecordStore(SessionStore, RecordStore):
    """Persists sessions and UseCaseRecords to local JSON files."""

    def __init__(self, root_dir: Path | str) -> None:
        self.root = Path(root_dir)
        self.sessions_dir = self.root / "sessions"
        self.records_dir = self.root / "records"
        self.portfolio_dir = self.root / "portfolio"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.portfolio_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, context_id: str) -> Path:
        safe_id = context_id.replace("/", "_")
        return self.sessions_dir / f"{safe_id}.json"

    def _record_path(self, record_id: str) -> Path:
        safe_id = record_id.replace("/", "_")
        return self.records_dir / f"{safe_id}.json"

    def load(self, context_id: str) -> Session | None:
        path = self._session_path(context_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _session_from_dict(data)
        except Exception as exc:
            log.warning("Failed to load session %s: %s", context_id, exc)
            return None

    def save(self, session: Session) -> None:
        session.touch()
        path = self._session_path(session.context_id)
        payload = json.dumps(_session_to_dict(session), indent=2)
        path.write_text(payload, encoding="utf-8")
        if session.record.meta.record_id:
            self.save_record(session.record)
            if session.is_complete:
                self._mark_completed(session)

    def _portfolio_path(self, record_id: str) -> Path:
        return self.portfolio_dir / f"{record_id.replace('/', '_')}.json"

    def _mark_completed(self, session: Session) -> None:
        path = self._portfolio_path(session.record.meta.record_id)
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        path.write_text(json.dumps(_completion_entry(session, existing), indent=2), encoding="utf-8")

    def list_completed(self, limit: int = 50) -> list[dict[str, Any]]:
        """Finished opportunities, newest first."""
        entries = []
        for path in self.portfolio_dir.glob("*.json"):
            try:
                entries.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("Skipping unreadable portfolio marker %s: %s", path.name, exc)
        entries.sort(key=lambda e: e.get("lastModifiedDateTime", ""), reverse=True)
        return entries[:limit]

    def load_portfolio_items(self, limit: int = 50) -> list[tuple[UseCaseRecord, dict[str, Any]]]:
        """`(record, listing_entry)` pairs for finished opportunities."""
        items = []
        for entry in self.list_completed(limit):
            record = self.load_record(entry["recordId"])
            if record is not None:
                items.append((record, entry))
        return items

    def delete(self, context_id: str) -> None:
        path = self._session_path(context_id)
        if path.is_file():
            path.unlink()

    def save_record(self, record: UseCaseRecord) -> str:
        path = self._record_path(record.meta.record_id)
        payload = json.dumps(record.model_dump(mode="json"), indent=2)
        path.write_text(payload, encoding="utf-8")
        return str(path)

    def load_record(self, record_id: str) -> UseCaseRecord | None:
        path = self._record_path(record_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return UseCaseRecord.model_validate(data)


class GCSRecordStore(SessionStore, RecordStore):
    """Persists sessions and UseCaseRecords to a Google Cloud Storage bucket.

    Requires zero Google Workspace licenses and works purely on GCP IAM.
    Solves L13 multi-instance session durability on Cloud Run and stores
    canonical JSON records under `gs://<bucket>/records/<UC-ID>.json`.
    """

    def __init__(self, bucket_name: str, client: Any = None) -> None:
        self.bucket_name = bucket_name
        if client is not None:
            self._client = client
        else:
            from google.cloud import storage  # type: ignore[import-untyped] # noqa: PLC0415

            self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def _session_blob(self, context_id: str) -> Any:
        safe_id = context_id.replace("/", "_")
        return self._bucket.blob(f"sessions/{safe_id}.json")

    def _record_blob(self, record_id: str) -> Any:
        safe_id = record_id.replace("/", "_")
        return self._bucket.blob(f"records/{safe_id}.json")

    def load(self, context_id: str) -> Session | None:
        blob = self._session_blob(context_id)
        if not blob.exists():
            return None
        try:
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            return _session_from_dict(data)
        except Exception as exc:
            log.warning("Failed to load GCS session %s: %s", context_id, exc)
            return None

    def save(self, session: Session) -> None:
        session.touch()
        blob = self._session_blob(session.context_id)
        payload = json.dumps(_session_to_dict(session), indent=2)
        blob.upload_from_string(payload, content_type="application/json")
        if session.record.meta.record_id:
            self.save_record(session.record)
            if session.is_complete:
                self._mark_completed(session)

    def _portfolio_blob(self, record_id: str) -> Any:
        return self._bucket.blob(f"portfolio/{record_id.replace('/', '_')}.json")

    def _mark_completed(self, session: Session) -> None:
        blob = self._portfolio_blob(session.record.meta.record_id)
        existing = None
        if blob.exists():
            try:
                existing = json.loads(blob.download_as_text(encoding="utf-8"))
            except Exception:
                existing = None
        blob.upload_from_string(
            json.dumps(_completion_entry(session, existing), indent=2),
            content_type="application/json",
        )

    def list_completed(self, limit: int = 50) -> list[dict[str, Any]]:
        """Finished opportunities, newest first."""
        entries = []
        for blob in self._client.list_blobs(self.bucket_name, prefix="portfolio/", max_results=500):
            try:
                entries.append(json.loads(blob.download_as_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("Skipping unreadable portfolio marker %s: %s", blob.name, exc)
        entries.sort(key=lambda e: e.get("lastModifiedDateTime", ""), reverse=True)
        return entries[:limit]

    def load_portfolio_items(self, limit: int = 50) -> list[tuple[UseCaseRecord, dict[str, Any]]]:
        """`(record, listing_entry)` pairs for finished opportunities."""
        items = []
        for entry in self.list_completed(limit):
            record = self.load_record(entry["recordId"])
            if record is not None:
                items.append((record, entry))
        return items

    def delete(self, context_id: str) -> None:
        blob = self._session_blob(context_id)
        if blob.exists():
            blob.delete()

    def save_record(self, record: UseCaseRecord) -> str:
        blob = self._record_blob(record.meta.record_id)
        payload = json.dumps(record.model_dump(mode="json"), indent=2)
        blob.upload_from_string(payload, content_type="application/json")
        return f"gs://{self.bucket_name}/records/{record.meta.record_id}.json"

    def load_record(self, record_id: str) -> UseCaseRecord | None:
        blob = self._record_blob(record_id)
        if not blob.exists():
            return None
        data = json.loads(blob.download_as_text(encoding="utf-8"))
        return UseCaseRecord.model_validate(data)


def create_default_store() -> SessionStore:
    """Returns GCSRecordStore if QUALIFY_GCS_BUCKET is set, LocalRecordStore if QUALIFY_DATA_DIR is set, else InMemorySessionStore."""
    bucket = os.environ.get("QUALIFY_GCS_BUCKET", "").strip()
    if bucket:
        log.info("Using GCSRecordStore with bucket gs://%s", bucket)
        return GCSRecordStore(bucket)

    data_dir = os.environ.get("QUALIFY_DATA_DIR", "").strip()
    if data_dir:
        log.info("Using LocalRecordStore at %s", data_dir)
        return LocalRecordStore(data_dir)

    from qualify.sinks.session import InMemorySessionStore  # noqa: PLC0415

    return InMemorySessionStore()
