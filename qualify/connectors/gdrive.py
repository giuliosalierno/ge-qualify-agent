"""Google Drive storage connector (Drive API v3 over REST, `drive.file` scope).

Mirrors the SharePoint connector's layout and auth contract so the agent can
switch providers with ``STORAGE_PROVIDER=gdrive``:

    My Drive/
      Qualification Opportunities/
        <Record ID> - <Initiative>/
          Business_Value_Brief.md
          Technical_Architecture_Dossier.md
          record.json
        Portfolio_Prioritization_Report.md

Scope and privacy: we request only ``drive.file``. The app can see files it
created and nothing else in the user's Drive. The consequence is that each
user's opportunities live in *their own* My Drive; there is no shared team
folder. That is the right trade-off for the demo (see the plan for why a
shared folder needs the restricted ``drive`` scope).

Auth modes (same vocabulary as SharePoint):

- ``delegated``: this conversation's signed-in Google user.
- ``mock``: local development (``GDRIVE_MOCK=1``, or no OAuth client set).
- ``unauthenticated``: OAuth is configured but this conversation has no
  signed-in user. There is no app-only fallback for Drive at all.
- ``error``: the Drive call failed. Nothing is claimed as saved.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from qualify.connectors import token_vault
from qualify.connectors.sharepoint import (
    sanitize_path_segment,
    split_opportunity_folder_name,
)
from qualify.connectors.storage import StorageAuthRequired, StorageSyncResult
from qualify.export import deliverable_filename, render_deliverable
from qualify.schema.use_case_record import UseCaseRecord

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"

#: Least privilege: only files this app creates. `openid email` let the
#: success page say which account connected, without any Drive-wide access.
GDRIVE_SCOPE = "openid email https://www.googleapis.com/auth/drive.file"

FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_FOLDER_NAME = "Qualification Opportunities"

#: Upper bound on any single upload. Deliverables are a few tens of KB.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _q_literal(value: str) -> str:
    """Quotes `value` as a Drive query string literal.

    Drive's `q` language uses single-quoted strings with backslash escapes. A
    folder name is user-derived (the initiative name), so an unescaped quote
    would let it rewrite the query.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _opportunity_entry(
    *,
    folder_name: str,
    web_url: str | None,
    filenames: set[str],
    modified: str | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    """One listing row, the same shape SharePoint returns."""
    record_id, initiative_name = split_opportunity_folder_name(folder_name)
    return {
        "id": item_id or folder_name,
        "name": folder_name,
        "recordId": record_id,
        "initiativeName": initiative_name or folder_name,
        "webUrl": web_url,
        "lastModifiedDateTime": modified,
        "hasBrief": deliverable_filename("business") in filenames,
        "hasDossier": deliverable_filename("tech") in filenames,
    }


class GoogleDriveConnector:
    """Reads and writes qualification opportunities in the user's Google Drive."""

    provider_id = "gdrive"
    display_name = "Google Drive"
    account_label = "Google"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        folder_name: str | None = None,
        mock_dir: Path | str | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("GDRIVE_OAUTH_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET", "")
        self.folder_name = sanitize_path_segment(
            folder_name or os.environ.get("GDRIVE_FOLDER_NAME", DEFAULT_FOLDER_NAME)
        )
        self._custom_mock_dir = Path(mock_dir) if mock_dir else None

    # -------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------

    @property
    def mock_dir(self) -> Path:
        if self._custom_mock_dir is not None:
            return self._custom_mock_dir
        return Path(os.environ.get("GDRIVE_MOCK_DIR", ".data/gdrive_mock"))

    def _is_mock(self) -> bool:
        return (
            self._custom_mock_dir is not None
            or os.environ.get("GDRIVE_MOCK") == "1"
            or not self.client_id
        )

    def get_headers(
        self, delegated_token: str | None = None, context_id: str | None = None
    ) -> tuple[dict[str, str], str]:
        """Authorization headers for one conversation, and the auth mode."""
        if self._is_mock():
            return {}, "mock"
        if delegated_token:
            return {"Authorization": f"Bearer {delegated_token}"}, "delegated"
        if context_id:
            token = token_vault.get_access(context_id) or self._refresh_user_token(context_id)
            if token:
                return {"Authorization": f"Bearer {token}"}, "delegated"
        return {}, "unauthenticated"

    def _refresh_user_token(self, context_id: str) -> str | None:
        """Exchanges this conversation's refresh token for a new access token."""
        refresh_token = token_vault.get_refresh(context_id)
        if not (refresh_token and self.client_id and self.client_secret):
            return None
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
            if resp.status_code != 200:
                logger.warning(
                    "Google refresh token rejected (%s); user must sign in again.",
                    resp.status_code,
                )
                token_vault.clear(context_id)
                return None
            data = resp.json()
            access_token = data["access_token"]
            # Google omits refresh_token on refresh; keep the one we have.
            token_vault.save_tokens(
                context_id,
                refresh_token=data.get("refresh_token", refresh_token),
                access_token=access_token,
                expires_in=int(data.get("expires_in", 3599)),
            )
            return access_token
        except Exception as exc:
            logger.warning("Google refresh_token flow failed (%s).", type(exc).__name__)
            return None

    def _on_unauthorized(self, context_id: str | None) -> None:
        """A 401 means the vaulted token is dead; forget it so we re-prompt."""
        if context_id:
            token_vault.clear(context_id)

    # -------------------------------------------------------------------
    # Drive primitives
    # -------------------------------------------------------------------

    def _find_child(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        name: str,
        parent_id: str,
        *,
        folder: bool,
    ) -> dict[str, Any] | None:
        clauses = [
            f"name = {_q_literal(name)}",
            f"{_q_literal(parent_id)} in parents",
            "trashed = false",
            f"mimeType {'=' if folder else '!='} {_q_literal(FOLDER_MIME)}",
        ]
        resp = client.get(
            f"{DRIVE_API}/files",
            headers=headers,
            params={
                "q": " and ".join(clauses),
                "fields": "files(id,name,webViewLink,modifiedTime)",
                "pageSize": "10",
                "spaces": "drive",
            },
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        return files[0] if files else None

    def _ensure_folder(
        self, client: httpx.Client, headers: dict[str, str], name: str, parent_id: str
    ) -> dict[str, Any]:
        existing = self._find_child(client, headers, name, parent_id, folder=True)
        if existing:
            return existing
        resp = client.post(
            f"{DRIVE_API}/files",
            headers=headers,
            params={"fields": "id,name,webViewLink"},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        resp.raise_for_status()
        return resp.json()

    def _upload(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        parent_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """Creates or overwrites `filename` in `parent_id`."""
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{filename} exceeds the {MAX_UPLOAD_BYTES}-byte upload limit")

        existing = self._find_child(client, headers, filename, parent_id, folder=False)
        if existing:
            resp = client.patch(
                f"{DRIVE_UPLOAD_API}/files/{urllib.parse.quote(existing['id'])}",
                headers={**headers, "Content-Type": mime_type},
                params={"uploadType": "media", "fields": "id,name,webViewLink"},
                content=content,
            )
            resp.raise_for_status()
            return resp.json()

        boundary = f"qualify-{secrets.token_hex(12)}"
        metadata = json.dumps({"name": filename, "parents": [parent_id]}).encode("utf-8")
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + metadata
            + f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        resp = client.post(
            f"{DRIVE_UPLOAD_API}/files",
            headers={**headers, "Content-Type": f"multipart/related; boundary={boundary}"},
            params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
            content=body,
        )
        resp.raise_for_status()
        return resp.json()

    def _list_children(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        parent_id: str,
        *,
        folders_only: bool = False,
        max_items: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = [f"{_q_literal(parent_id)} in parents", "trashed = false"]
        if folders_only:
            clauses.append(f"mimeType = {_q_literal(FOLDER_MIME)}")
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(items) < max_items:
            params = {
                "q": " and ".join(clauses),
                "fields": "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime)",
                "pageSize": "100",
                "orderBy": "name",
                "spaces": "drive",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = client.get(f"{DRIVE_API}/files", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return items[:max_items]

    def _download_text(
        self, client: httpx.Client, headers: dict[str, str], file_id: str
    ) -> str:
        resp = client.get(
            f"{DRIVE_API}/files/{urllib.parse.quote(file_id)}",
            headers=headers,
            params={"alt": "media"},
        )
        resp.raise_for_status()
        if len(resp.content) > MAX_UPLOAD_BYTES:
            raise ValueError("record.json is larger than expected")
        return resp.text

    def _root_folder(
        self, client: httpx.Client, headers: dict[str, str], *, create: bool
    ) -> dict[str, Any] | None:
        if create:
            return self._ensure_folder(client, headers, self.folder_name, "root")
        return self._find_child(client, headers, self.folder_name, "root", folder=True)

    # -------------------------------------------------------------------
    # StorageConnector API
    # -------------------------------------------------------------------

    def sync_opportunity(
        self,
        record: UseCaseRecord,
        *,
        skipped_stages: set[int] | None = None,
        delegated_token: str | None = None,
        context_id: str | None = None,
        pack_name: str = "business",
    ) -> StorageSyncResult:
        """Writes the pack's deliverable and `record.json` to the record folder."""
        headers, auth_mode = self.get_headers(delegated_token, context_id)
        record_id = sanitize_path_segment(record.meta.record_id or "UC-UNKNOWN")
        if auth_mode == "unauthenticated":
            return StorageSyncResult(
                success=False,
                record_id=record.meta.record_id,
                folder_url="",
                brief_url="",
                auth_mode=auth_mode,
                message="Google sign-in required before saving to Google Drive.",
            )

        init_name = sanitize_path_segment(record.meta.initiative_name or "Untitled Initiative")
        folder_name = f"{record_id} - {init_name}"
        filename = deliverable_filename(pack_name)
        brief_md = render_deliverable(pack_name, record, skipped_stages=skipped_stages)
        record_json = record.model_dump_json(indent=2)

        if auth_mode == "mock":
            return self._sync_mock(record_id, folder_name, filename, brief_md, record_json)

        try:
            with httpx.Client(timeout=12.0) as client:
                root = self._root_folder(client, headers, create=True)
                if root is None:
                    raise RuntimeError("Drive root folder could not be created")
                folder = self._ensure_folder(client, headers, folder_name, root["id"])
                brief = self._upload(
                    client, headers, folder["id"], filename,
                    brief_md.encode("utf-8"), "text/markdown",
                )
                self._upload(
                    client, headers, folder["id"], "record.json",
                    record_json.encode("utf-8"), "application/json",
                )
            return StorageSyncResult(
                success=True,
                record_id=record.meta.record_id,
                folder_url=folder.get("webViewLink", ""),
                brief_url=brief.get("webViewLink", ""),
                auth_mode="delegated",
                message=f"Synced {record.meta.record_id} to Google Drive.",
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("Google Drive sync failed with HTTP %s.", status)
            if status == 401:
                self._on_unauthorized(context_id)
                return StorageSyncResult(
                    success=False, record_id=record.meta.record_id, folder_url="",
                    brief_url="", auth_mode="unauthenticated",
                    message="Google session expired; sign in again.",
                )
        except Exception as exc:
            logger.warning("Google Drive sync failed (%s).", type(exc).__name__)
        return StorageSyncResult(
            success=False,
            record_id=record.meta.record_id,
            folder_url="",
            brief_url="",
            auth_mode="error",
            message="Google Drive could not be reached; nothing was saved.",
        )

    def list_opportunities(
        self,
        query: str = "",
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        pending_technical_review: bool = False,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Lists opportunity folders, optionally only those awaiting a dossier."""
        headers, auth_mode = self.get_headers(delegated_token, context_id)
        if auth_mode == "unauthenticated":
            raise StorageAuthRequired("Google sign-in required to list Google Drive opportunities.")
        q_norm = query.strip().lower()

        if auth_mode == "mock":
            entries = self._list_mock(q_norm)
        else:
            entries = self._list_live(headers, q_norm, limit, context_id)

        if pending_technical_review:
            entries = [e for e in entries if e["hasBrief"] and not e["hasDossier"]]
        return entries[:limit]

    def _list_live(
        self,
        headers: dict[str, str],
        q_norm: str,
        limit: int,
        context_id: str | None,
    ) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=10.0) as client:
                root = self._root_folder(client, headers, create=False)
                if root is None:
                    return []
                results: list[dict[str, Any]] = []
                for folder in self._list_children(client, headers, root["id"], folders_only=True):
                    name = folder.get("name", "")
                    if not name or (q_norm and q_norm not in name.lower()):
                        continue
                    filenames: set[str] = set()
                    if len(results) < limit:
                        filenames = {
                            f.get("name", "")
                            for f in self._list_children(client, headers, folder["id"], max_items=50)
                        }
                    results.append(
                        _opportunity_entry(
                            folder_name=name,
                            web_url=folder.get("webViewLink"),
                            modified=folder.get("modifiedTime"),
                            filenames=filenames,
                            item_id=folder.get("id"),
                        )
                    )
                return results
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self._on_unauthorized(context_id)
                raise StorageAuthRequired("Google session expired; sign in again.") from exc
            raise RuntimeError(f"Google Drive listing failed (HTTP {exc.response.status_code})") from exc

    def load_opportunity(
        self,
        query_or_record_id: str,
        delegated_token: str | None = None,
        context_id: str | None = None,
    ) -> UseCaseRecord | None:
        """Loads a record by record id or initiative-name fragment."""
        target = query_or_record_id.strip().lower()
        if not target:
            return None
        headers, auth_mode = self.get_headers(delegated_token, context_id)
        if auth_mode == "unauthenticated":
            raise StorageAuthRequired("Google sign-in required to load from Google Drive.")

        if auth_mode == "mock":
            for child in self._mock_folders():
                if target in child.name.lower() and (child / "record.json").is_file():
                    return UseCaseRecord.model_validate_json(
                        (child / "record.json").read_text(encoding="utf-8")
                    )
            return None

        try:
            with httpx.Client(timeout=10.0) as client:
                root = self._root_folder(client, headers, create=False)
                if root is None:
                    return None
                for folder in self._list_children(client, headers, root["id"], folders_only=True):
                    if target not in folder.get("name", "").lower():
                        continue
                    rec_file = self._find_child(
                        client, headers, "record.json", folder["id"], folder=False
                    )
                    if rec_file:
                        return UseCaseRecord.model_validate_json(
                            self._download_text(client, headers, rec_file["id"])
                        )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self._on_unauthorized(context_id)
                raise StorageAuthRequired("Google session expired; sign in again.") from exc
            logger.warning("Google Drive load failed (HTTP %s).", exc.response.status_code)
        return None

    def load_all_opportunities(
        self,
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        limit: int = 50,
    ) -> list[tuple[UseCaseRecord, dict[str, Any]]]:
        """Every opportunity with a valid `record.json`, with its listing entry."""
        entries = self.list_opportunities(
            delegated_token=delegated_token, context_id=context_id, limit=limit
        )
        if not entries:
            return []
        headers, auth_mode = self.get_headers(delegated_token, context_id)
        loaded: list[tuple[UseCaseRecord, dict[str, Any]]] = []

        if auth_mode == "mock":
            base = self._mock_root()
            for entry in entries:
                rec_path = base / sanitize_path_segment(entry["name"]) / "record.json"
                if rec_path.is_file():
                    try:
                        loaded.append(
                            (UseCaseRecord.model_validate_json(rec_path.read_text(encoding="utf-8")), entry)
                        )
                    except Exception as exc:
                        logger.warning("Skipping invalid mock record.json (%s).", type(exc).__name__)
            return loaded

        with httpx.Client(timeout=12.0) as client:
            for entry in entries:
                try:
                    rec_file = self._find_child(
                        client, headers, "record.json", entry["id"], folder=False
                    )
                    if rec_file:
                        loaded.append(
                            (
                                UseCaseRecord.model_validate_json(
                                    self._download_text(client, headers, rec_file["id"])
                                ),
                                entry,
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "Skipping unreadable record.json in %s (%s).",
                        entry.get("name"), type(exc).__name__,
                    )
        return loaded

    def sync_portfolio_report(
        self,
        report_md: str,
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        filename: str = "Portfolio_Prioritization_Report.md",
    ) -> str | None:
        """Uploads the portfolio report next to the opportunity folders."""
        headers, auth_mode = self.get_headers(delegated_token, context_id)
        if auth_mode == "unauthenticated":
            return None
        safe_filename = sanitize_path_segment(filename)

        if auth_mode == "mock":
            base = self._mock_root()
            base.mkdir(parents=True, exist_ok=True)
            (base / safe_filename).write_text(report_md, encoding="utf-8")
            return f"https://drive.mock/{urllib.parse.quote(self.folder_name)}/{urllib.parse.quote(safe_filename)}"

        try:
            with httpx.Client(timeout=12.0) as client:
                root = self._root_folder(client, headers, create=True)
                if root is None:
                    raise RuntimeError("Drive root folder could not be created")
                uploaded = self._upload(
                    client, headers, root["id"], safe_filename,
                    report_md.encode("utf-8"), "text/markdown",
                )
            return uploaded.get("webViewLink")
        except Exception as exc:
            logger.warning("Google Drive portfolio upload failed (%s).", type(exc).__name__)
            return None

    # -------------------------------------------------------------------
    # Local mock (development and tests)
    # -------------------------------------------------------------------

    def _mock_root(self) -> Path:
        return self.mock_dir / self.folder_name

    def _mock_folders(self) -> list[Path]:
        root = self._mock_root()
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir())

    def _sync_mock(
        self, record_id: str, folder_name: str, filename: str, brief_md: str, record_json: str
    ) -> StorageSyncResult:
        folder = self._mock_root() / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(brief_md, encoding="utf-8")
        (folder / "record.json").write_text(record_json, encoding="utf-8")
        folder_url = f"https://drive.mock/{urllib.parse.quote(self.folder_name)}/{urllib.parse.quote(folder_name)}"
        return StorageSyncResult(
            success=True,
            record_id=record_id,
            folder_url=folder_url,
            brief_url=f"{folder_url}/{urllib.parse.quote(filename)}",
            auth_mode="mock",
            message=f"Synced {record_id} to the local Google Drive mock.",
        )

    def _list_mock(self, q_norm: str) -> list[dict[str, Any]]:
        return [
            _opportunity_entry(
                folder_name=child.name,
                web_url=f"https://drive.mock/{urllib.parse.quote(child.name)}",
                filenames={f.name for f in child.iterdir() if f.is_file()},
            )
            for child in self._mock_folders()
            if not q_norm or q_norm in child.name.lower()
        ]


_CONNECTOR_INSTANCE: GoogleDriveConnector | None = None


def get_gdrive_connector() -> GoogleDriveConnector:
    """Returns the default GoogleDriveConnector singleton."""
    global _CONNECTOR_INSTANCE
    if _CONNECTOR_INSTANCE is None:
        _CONNECTOR_INSTANCE = GoogleDriveConnector()
    return _CONNECTOR_INSTANCE
