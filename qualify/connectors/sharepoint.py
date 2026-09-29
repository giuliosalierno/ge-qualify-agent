"""SharePoint Connector Layer (Microsoft Graph REST API + Dual-Layer OAuth 2.0).

Supports:
1. Dual-Layer Authentication:
   - Layer 1: Delegated User Identity (Bearer token forwarded from Gemini Enterprise MCP OAuth).
   - Layer 2: Application Client Credentials (MS_GRAPH_TENANT_ID, MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET).
   - Layer 3: Local Mock Mode (when credentials are omitted or SHAREPOINT_MOCK=1).
2. Opportunity Storage & Retrieval:
   - Document Library Folder per opportunity:
     `Qualification Opportunities/<Record_ID> - <Initiative_Name>/Business_Value_Brief.md`
     `Qualification Opportunities/<Record_ID> - <Initiative_Name>/record.json`
   - SharePoint List Inventory row (`AI Use Case Inventory`).
3. Safe Document Text Extraction (`.docx`, `.pptx`, `.xlsx`, `.txt`, `.md`).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import httpx

from qualify.connectors import storage, token_vault
from qualify.connectors.storage import StorageAuthRequired, StorageSyncResult
from qualify.export import deliverable_filename, render_deliverable
from qualify.schema.use_case_record import UseCaseRecord
from qualify.scoring import classify_capability

logger = logging.getLogger(__name__)

# In-memory, per-conversation vault: context_id -> (access_token, expires_at_unix_ts).
# The same dict object as `token_vault.ACCESS`; see that module for the
# security model. Kept under this name for existing callers and tests.
_TOKEN_VAULT: dict[str, tuple[str, float]] = token_vault.ACCESS

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_FOLDER_PATH = "Qualification Opportunities"
DEFAULT_LIST_NAME = "AI Use Case Inventory"

#: Historical name for the provider-neutral result type.
SharePointSyncResult = StorageSyncResult


def sanitize_path_segment(segment: str) -> str:
    """Sanitizes a folder or file name segment to prevent path traversal and invalid SharePoint characters.

    Strips directory separators and restricts characters to a safe allow-list.
    """
    base = os.path.basename(segment.strip())
    # Replace SharePoint forbidden characters: " * : < > ? / \ |
    cleaned = re.sub(r'["*:<>?/\\|]', "_", base)
    cleaned = re.sub(r"\.\.+", ".", cleaned)
    return cleaned.strip(" .") or "unnamed"


#: Matches a record id occupying a whole folder-name segment.
#:
#: Deliberately a separate pattern from `handover.RECORD_ID_RE`, which searches
#: inside free chat text and therefore anchors on word boundaries. This one
#: full-matches a segment already split off a folder name. Keeping them apart
#: means neither has to be loosened to serve the other, and the connector does
#: not import from the agent package to get it.
_FOLDER_RECORD_ID_RE = re.compile(r"UC-\d{4}-[A-Z0-9]+", re.IGNORECASE)


def split_opportunity_folder_name(folder_name: str) -> tuple[str, str]:
    """Splits `UC-2026-A1B2C3 - Invoice Triage` into its id and its name.

    `sync_opportunity` is the only writer of these folders and always uses
    `{record_id} - {initiative_name}`, so this reverses a known format rather
    than guessing at one. The split takes the *first* separator only, leaving
    an initiative whose own name contains " - " intact.

    A folder that does not follow the convention — created by hand, or renamed
    — yields an empty id and the whole string as the name. It can still be
    listed and linked; it simply cannot be offered as a review target, which
    is the honest outcome when we do not know its record id.
    """
    parts = folder_name.split(" - ", 1)
    candidate = parts[0].strip()
    if _FOLDER_RECORD_ID_RE.fullmatch(candidate):
        return candidate.upper(), (parts[1].strip() if len(parts) > 1 else "")
    return "", folder_name.strip()


def _expanded_child_filenames(item: dict[str, Any]) -> set[str] | None:
    """Filenames from a Graph `$expand=children` payload, or `None` if absent.

    The distinction matters: an empty folder and a folder Graph declined to
    expand both look like "no files", and treating the second as the first
    would report every opportunity as missing its brief.
    """
    children = item.get("children")
    if not isinstance(children, list):
        return None
    return {c.get("name", "") for c in children if isinstance(c, dict)}


def is_microsoft_graph_token(token: str) -> bool:
    """Returns True if the token is a Microsoft Entra / Graph token (and not a Google Cloud OIDC IAM token).

    When Cloud Run IAM is enabled, the HTTP Authorization header carries a Google OIDC token
    (iss=https://accounts.google.com). This check ensures we never mistake a Google IAM token
    for a Microsoft Graph Bearer token.
    """
    clean = token.strip()
    if clean.lower().startswith("bearer "):
        clean = clean[7:].strip()
    if not clean or clean == "mock" or clean.startswith("mock_"):
        return False
    parts = clean.split(".")
    if len(parts) == 3:
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            iss = str(payload.get("iss", "")).lower()
            aud = str(payload.get("aud", "")).lower()
            if "google.com" in iss or "googleapis.com" in aud:
                return False
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# Per-conversation token vault (memory only)
# ---------------------------------------------------------------------------
#
# The vault itself lives in `token_vault.py`, shared with the Google Drive
# connector; its docstring holds the full security model. SharePoint-specific
# points:
# - Only tokens that look like Microsoft Graph tokens are vaulted here
#   (`is_microsoft_graph_token`), so a Google IAM token can never be mistaken
#   for a user's Microsoft token.
# - Tokens are never harvested from inbound A2A requests: Gemini Enterprise
#   does not forward the user's Microsoft token (verified in Cloud Run logs),
#   and an inbound header is no proof of which conversation it belongs to.

DELEGATED_GRAPH_SCOPE = "https://graph.microsoft.com/Sites.ReadWrite.All offline_access"

# context_id -> refresh_token (same object as `token_vault.REFRESH`)
_REFRESH_VAULT: dict[str, str] = token_vault.REFRESH
# App-only token cache, used only when SHAREPOINT_APP_AUTH=1.
_APP_TOKEN: dict[str, tuple[str, float]] = {}

#: Historical name for the provider-neutral auth error. Same class, so
#: `except SharePointAuthRequired` also catches errors from other providers.
SharePointAuthRequired = StorageAuthRequired


def _strip_bearer(token: str) -> str:
    clean = token.strip()
    return clean[7:].strip() if clean.lower().startswith("bearer ") else clean


def cache_delegated_token(token: str, key: str, ttl_seconds: int = 3600) -> None:
    """Caches a delegated Microsoft Graph access token for one conversation."""
    if not key:
        return
    clean = _strip_bearer(token)
    if is_microsoft_graph_token(clean):
        token_vault.put_access(key, clean, ttl_seconds)


def get_cached_delegated_token(key: str | None) -> str | None:
    """Returns this conversation's non-expired access token, or None."""
    return token_vault.get_access(key)


def save_delegated_refresh_token(
    refresh_token: str,
    access_token: str = "",
    expires_in: int = 3599,
    *,
    context_id: str,
) -> None:
    """Vaults a user's delegated tokens in memory, for exactly one conversation."""
    if not context_id:
        raise ValueError("context_id is required to store user tokens")
    if refresh_token:
        token_vault.save_tokens(context_id, refresh_token=refresh_token)
    if access_token:
        cache_delegated_token(access_token, key=context_id, ttl_seconds=max(60, expires_in - 60))


def load_delegated_refresh_token(context_id: str | None) -> str:
    """Returns this conversation's refresh token, or an empty string."""
    return token_vault.get_refresh(context_id)


def has_user_session(context_id: str | None) -> bool:
    """True if this conversation holds a usable access token or a refresh token."""
    return token_vault.has_session(context_id)


def clear_user_tokens(context_id: str) -> None:
    """Forgets every token held for a conversation."""
    token_vault.clear(context_id)


# Pending records awaiting user authentication, shared with other providers:
# context_id -> (UseCaseRecord, skipped_stages, pack_name)
_PENDING_RECORDS: dict[str, tuple[UseCaseRecord, set[int], str]] = token_vault.PENDING


def auto_sync_pending_records(access_token: str, context_id: str) -> SharePointSyncResult | None:
    """Syncs this conversation's pending UseCaseRecord immediately after its user signs in."""
    if not context_id:
        return None
    try:
        connector = get_sharepoint_connector()
    except Exception as exc:
        logger.warning("Auto-sync after sign-in failed: %s", exc)
        return None
    return storage.auto_sync_pending(access_token, context_id, connector=connector)


class SharePointConnector:
    """Client for reading and writing qualification opportunities in SharePoint Online."""

    provider_id = "sharepoint"
    display_name = "Microsoft SharePoint"
    account_label = "Microsoft"

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        site_name: str | None = None,
        drive_name: str | None = None,
        folder_path: str | None = None,
        list_name: str | None = None,
        mock_dir: Path | str | None = None,
    ) -> None:
        self.tenant_id = tenant_id or os.environ.get("MS_GRAPH_TENANT_ID", "")
        self.client_id = client_id or os.environ.get("MS_GRAPH_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("MS_GRAPH_CLIENT_SECRET", "")
        self.site_name = site_name or os.environ.get("SHAREPOINT_SITE_NAME", "")
        self.drive_name = drive_name or os.environ.get("SHAREPOINT_DRIVE_NAME", "Documents")
        self.folder_path = folder_path or os.environ.get("SHAREPOINT_FOLDER_PATH", DEFAULT_FOLDER_PATH)
        self.list_name = list_name or os.environ.get("SHAREPOINT_LIST_NAME", DEFAULT_LIST_NAME)
        self._custom_mock_dir = Path(mock_dir) if mock_dir else None
        self._guid_cache: dict[str, str] = {}

    @property
    def mock_dir(self) -> Path:
        if self._custom_mock_dir is not None:
            return self._custom_mock_dir
        return Path(os.environ.get("SHAREPOINT_MOCK_DIR", ".data/sharepoint_mock"))

    def get_graph_headers(
        self,
        delegated_token: str | None = None,
        context_id: str | None = None,
    ) -> tuple[dict[str, str], str]:
        """Resolves Microsoft Graph Authorization headers for one conversation.

        Returns:
            (headers_dict, auth_mode) where auth_mode is one of:
            - 'delegated': the signed-in user of *this* conversation.
            - 'client_credentials': app-only access, only when explicitly
              enabled with SHAREPOINT_APP_AUTH=1.
            - 'mock': local development (SHAREPOINT_MOCK=1, or Entra not
              configured at all).
            - 'unauthenticated': Entra is configured but this conversation has
              no signed-in user. Callers must ask the user to sign in; there is
              deliberately no silent fallback to another identity.
        """
        if os.environ.get("SHAREPOINT_MOCK") == "1":
            return {"Authorization": "Bearer mock_graph_token", "Accept": "application/json"}, "mock"

        # 1. A token supplied with this request (e.g. /mcp). Used once, never vaulted:
        #    an inbound header is not proof of which conversation it belongs to.
        if delegated_token:
            clean = _strip_bearer(delegated_token)
            if is_microsoft_graph_token(clean):
                return {"Authorization": f"Bearer {clean}", "Accept": "application/json"}, "delegated"

        # 2. This conversation's own vaulted token, refreshed if needed.
        if context_id:
            cached = get_cached_delegated_token(context_id)
            if cached:
                return {"Authorization": f"Bearer {cached}", "Accept": "application/json"}, "delegated"
            refreshed = self._refresh_user_token(context_id)
            if refreshed:
                return {"Authorization": f"Bearer {refreshed}", "Accept": "application/json"}, "delegated"

        # 3. Entra not configured: local development against the mock directory.
        if not (self.tenant_id and self.client_id):
            return {"Authorization": "Bearer mock_graph_token", "Accept": "application/json"}, "mock"

        # 4. App-only access is opt-in. It bypasses per-user SharePoint permissions.
        if os.environ.get("SHAREPOINT_APP_AUTH") == "1" and self.client_secret:
            app_token = self._app_token()
            if app_token:
                return {"Authorization": f"Bearer {app_token}", "Accept": "application/json"}, "client_credentials"

        return {"Accept": "application/json"}, "unauthenticated"

    def _token_url(self) -> str:
        return f"https://login.microsoftonline.com/{urllib.parse.quote(self.tenant_id)}/oauth2/v2.0/token"

    def _refresh_user_token(self, context_id: str) -> str | None:
        """Exchanges this conversation's refresh token for a new access token."""
        refresh_token = load_delegated_refresh_token(context_id)
        if not (refresh_token and self.tenant_id and self.client_id):
            return None
        payload = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": DELEGATED_GRAPH_SCOPE,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(self._token_url(), data=payload)
            if resp.status_code != 200:
                logger.warning("Refresh token rejected by Entra (%s); user must sign in again.", resp.status_code)
                clear_user_tokens(context_id)
                return None
            data = resp.json()
            access_token = data["access_token"]
            save_delegated_refresh_token(
                data.get("refresh_token", refresh_token),
                access_token,
                int(data.get("expires_in", 3599)),
                context_id=context_id,
            )
            return access_token
        except Exception as exc:
            logger.warning("Entra refresh_token flow failed (%s).", type(exc).__name__)
            return None

    def _app_token(self) -> str | None:
        """App-only client_credentials token. Only reached when SHAREPOINT_APP_AUTH=1."""
        entry = _APP_TOKEN.get("app")
        if entry and time.time() < entry[1]:
            return entry[0]
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(
                    self._token_url(),
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "https://graph.microsoft.com/.default",
                        "grant_type": "client_credentials",
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            _APP_TOKEN["app"] = (data["access_token"], time.time() + max(60, int(data.get("expires_in", 3599)) - 60))
            return data["access_token"]
        except Exception as exc:
            logger.warning("Entra client_credentials flow failed (%s).", type(exc).__name__)
            return None

    # -----------------------------------------------------------------------
    # Smart Name-to-GUID Resolvers (Ported from  SharePoint MCP Server)
    # -----------------------------------------------------------------------

    def resolve_site_id(self, headers: dict[str, str], site_query: str | None = None) -> str:
        """Resolves a human-readable SharePoint site name (or 'root') to a Graph Site ID."""
        target = site_query or self.site_name or "root"
        cache_key = f"site:{target}"
        if cache_key in self._guid_cache:
            return self._guid_cache[cache_key]

        if target.lower() == "root" or "," in target:
            self._guid_cache[cache_key] = target
            return target

        with httpx.Client(timeout=8.0) as client:
            resp = client.get(
                f"{GRAPH_BASE_URL}/sites?search={urllib.parse.quote(target)}",
                headers=headers,
            )
            resp.raise_for_status()
            sites = resp.json().get("value", [])
            for site in sites:
                if (site.get("displayName") or "").lower() == target.lower() or (site.get("name") or "").lower() == target.lower():
                    self._guid_cache[cache_key] = site["id"]
                    return site["id"]
            if sites:
                self._guid_cache[cache_key] = sites[0]["id"]
                return sites[0]["id"]

        return "root"

    def resolve_drive_id(self, headers: dict[str, str], site_id: str = "root", drive_name: str | None = None) -> str:
        """Resolves a document library name (e.g. 'Documents') to its Graph Drive GUID."""
        target = drive_name or self.drive_name or "Documents"
        cache_key = f"drive:{site_id}:{target}"
        if cache_key in self._guid_cache:
            return self._guid_cache[cache_key]

        if len(target) > 20 or target.startswith("b!"):
            self._guid_cache[cache_key] = target
            return target

        with httpx.Client(timeout=8.0) as client:
            endpoint = f"{GRAPH_BASE_URL}/sites/{urllib.parse.quote(site_id)}/drives"
            resp = client.get(endpoint, headers=headers)
            resp.raise_for_status()
            drives = resp.json().get("value", [])
            for drv in drives:
                if (drv.get("name") or "").lower() == target.lower():
                    self._guid_cache[cache_key] = drv["id"]
                    return drv["id"]
            if drives:
                self._guid_cache[cache_key] = drives[0]["id"]
                return drives[0]["id"]

        raise ValueError(f"Could not resolve SharePoint drive {target!r} in site {site_id!r}")

    def resolve_list_id(self, headers: dict[str, str], site_id: str = "root", list_name: str | None = None) -> str | None:
        """Resolves a SharePoint List name (e.g. 'AI Use Case Inventory') to its Graph List GUID."""
        target = list_name or self.list_name
        if not target:
            return None
        cache_key = f"list:{site_id}:{target}"
        if cache_key in self._guid_cache:
            return self._guid_cache[cache_key]

        with httpx.Client(timeout=8.0) as client:
            endpoint = f"{GRAPH_BASE_URL}/sites/{urllib.parse.quote(site_id)}/lists"
            resp = client.get(endpoint, headers=headers)
            if resp.status_code != 200:
                return None
            lists = resp.json().get("value", [])
            for lst in lists:
                if (lst.get("displayName") or "").lower() == target.lower() or (lst.get("name") or "").lower() == target.lower():
                    self._guid_cache[cache_key] = lst["id"]
                    return lst["id"]
        return None

    # -----------------------------------------------------------------------
    # Write Operations: Dual Sync (Folder + Brief + record.json + List Item)
    # -----------------------------------------------------------------------

    def sync_opportunity(
        self,
        record: UseCaseRecord,
        *,
        skipped_stages: set[int] | None = None,
        delegated_token: str | None = None,
        context_id: str | None = None,
        pack_name: str = "business",
    ) -> SharePointSyncResult:
        """Synchronizes a UseCaseRecord and its pack's deliverable to SharePoint.

        `pack_name` picks the deliverable. Both packs write into the *same*
        record folder under different filenames, so a technical review adds the
        dossier alongside the business brief rather than overwriting it.
        """
        filename = deliverable_filename(pack_name)
        headers, auth_mode = self.get_graph_headers(delegated_token, context_id=context_id)
        if auth_mode == "unauthenticated" and self._custom_mock_dir is None:
            return SharePointSyncResult(
                success=False,
                record_id=record.meta.record_id,
                folder_url="",
                brief_url="",
                auth_mode=auth_mode,
                message="Microsoft sign-in required before saving to SharePoint.",
            )
        record_id = sanitize_path_segment(record.meta.record_id or "UC-UNKNOWN")
        init_name = sanitize_path_segment(record.meta.initiative_name or "Untitled Initiative")
        folder_name = f"{record_id} - {init_name}"

        brief_md = render_deliverable(pack_name, record, skipped_stages=skipped_stages)
        record_json = record.model_dump_json(indent=2)
        classify_capability(record)
        cap_level = record.technical.capability_level
        cap_str = cap_level.value if cap_level else ""
        total_hours = record.derived.total_annual_team_hours_saved or 0.0
        gate1_status = (
            "CONDITIONAL — OPEN DISCOVERY ITEMS PENDING"
            if skipped_stages
            else "QUALIFIED FOR GATE 1 (TECHNICAL & COE REVIEW)"
        )

        list_fields = {
            "Title": record.meta.initiative_name or record_id,
            "RecordId": record.meta.record_id,
            "Department": record.meta.department_bu or record.business.user_profile or "",
            "BusinessOwner": record.proposed.business_owner or record.meta.submitter or "",
            "ExecutiveSponsor": record.proposed.executive_sponsor or "",
            "AnnualHoursSaved": round(float(total_hours), 1),
            "RecommendedCapability": cap_str,
            "Gate1Status": gate1_status,
        }

        if auth_mode == "mock" or self._custom_mock_dir is not None:
            return self._sync_mock(
                record_id, folder_name, brief_md, record_json, list_fields,
                auth_mode=auth_mode, filename=filename,
            )

        try:
            site_id = self.resolve_site_id(headers)
            drive_id = self.resolve_drive_id(headers, site_id=site_id)
            parent_folder = sanitize_path_segment(self.folder_path)
            rel_folder_path = f"{parent_folder}/{folder_name}"

            with httpx.Client(timeout=12.0) as client:
                # 1. Upload the pack's deliverable
                brief_endpoint = (
                    f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                    f"/root:/{urllib.parse.quote(rel_folder_path)}/{filename}:/content"
                )
                brief_resp = client.put(
                    brief_endpoint,
                    content=brief_md.encode("utf-8"),
                    headers={**headers, "Content-Type": "text/markdown; charset=utf-8"},
                )
                brief_resp.raise_for_status()
                brief_data = brief_resp.json()
                brief_url = brief_data.get("webUrl", "")
                folder_web_url = brief_url.rsplit("/", 1)[0] if "/" in brief_url else brief_url

                # 2. Upload record.json
                json_endpoint = (
                    f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                    f"/root:/{urllib.parse.quote(rel_folder_path)}/record.json:/content"
                )
                json_resp = client.put(
                    json_endpoint,
                    content=record_json.encode("utf-8"),
                    headers={**headers, "Content-Type": "application/json; charset=utf-8"},
                )
                json_resp.raise_for_status()

                # 3. Upsert SharePoint List Item (if list exists)
                list_item_id: str | None = None
                list_id = self.resolve_list_id(headers, site_id=site_id)
                if list_id:
                    list_fields["FolderUrl"] = folder_web_url
                    list_item_id = self._upsert_graph_list_item(client, headers, site_id, list_id, record.meta.record_id, list_fields)

            return SharePointSyncResult(
                success=True,
                record_id=record.meta.record_id,
                folder_url=folder_web_url,
                brief_url=brief_url,
                list_item_id=list_item_id,
                auth_mode=auth_mode,
                message=f"Synced {record.meta.record_id} to SharePoint ({auth_mode} mode).",
            )
        except Exception as exc:
            logger.warning("Live SharePoint Graph sync encountered error (%s), saving to local mock fallback.", exc)
            return self._sync_mock(
                record_id, folder_name, brief_md, record_json, list_fields,
                filename=filename,
            )

    def _upsert_graph_list_item(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        site_id: str,
        list_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> str | None:
        """Creates or updates a row in a SharePoint List."""
        items_url = f"{GRAPH_BASE_URL}/sites/{urllib.parse.quote(site_id)}/lists/{urllib.parse.quote(list_id)}/items"
        resp = client.get(f"{items_url}?$expand=fields", headers=headers)
        if resp.status_code == 200:
            for item in resp.json().get("value", []):
                item_fields = item.get("fields", {})
                if item_fields.get("RecordId") == record_id or item_fields.get("Title") == record_id:
                    item_id = item["id"]
                    patch_url = f"{items_url}/{urllib.parse.quote(str(item_id))}/fields"
                    p_resp = client.patch(patch_url, json=fields, headers=headers)
                    if p_resp.status_code in (200, 204):
                        return str(item_id)

        post_resp = client.post(items_url, json={"fields": fields}, headers=headers)
        if post_resp.status_code in (200, 201):
            return str(post_resp.json().get("id", ""))
        return None

    def _sync_mock(
        self,
        record_id: str,
        folder_name: str,
        brief_md: str,
        record_json: str,
        list_fields: dict[str, Any],
        auth_mode: str = "mock",
        filename: str = "Business_Value_Brief.md",
    ) -> SharePointSyncResult:
        """Writes SharePoint folder & list state to local filesystem mock directory."""
        base_folder = self.mock_dir / "drives" / sanitize_path_segment(self.drive_name) / sanitize_path_segment(self.folder_path) / folder_name
        base_folder.mkdir(parents=True, exist_ok=True)

        brief_path = base_folder / filename
        json_path = base_folder / "record.json"
        brief_path.write_text(brief_md, encoding="utf-8")
        json_path.write_text(record_json, encoding="utf-8")

        # Upsert into local mock SharePoint List JSON file
        list_dir = self.mock_dir / "lists" / sanitize_path_segment(self.list_name)
        list_dir.mkdir(parents=True, exist_ok=True)
        items_file = list_dir / "items.json"
        items: list[dict[str, Any]] = []
        if items_file.is_file():
            try:
                items = json.loads(items_file.read_text(encoding="utf-8"))
            except Exception:
                items = []

        folder_url = f"https://sharepoint.mock/sites/AI-CoE/{urllib.parse.quote(self.folder_path)}/{urllib.parse.quote(folder_name)}"
        brief_url = f"{folder_url}/{filename}"
        list_fields["FolderUrl"] = folder_url

        updated = False
        list_item_id = f"sp-item-{record_id}"
        for idx, existing in enumerate(items):
            if existing.get("fields", {}).get("RecordId") == record_id:
                items[idx] = {"id": existing.get("id", list_item_id), "fields": list_fields}
                list_item_id = items[idx]["id"]
                updated = True
                break
        if not updated:
            items.append({"id": list_item_id, "fields": list_fields})

        items_file.write_text(json.dumps(items, indent=2), encoding="utf-8")

        return SharePointSyncResult(
            success=True,
            record_id=record_id,
            folder_url=folder_url,
            brief_url=brief_url,
            list_item_id=list_item_id,
            auth_mode=auth_mode,
            message=f"Synced {record_id} to SharePoint local mock ({base_folder}).",
        )

    # -----------------------------------------------------------------------
    # Read Operations: Search & Load Opportunity into Session
    # -----------------------------------------------------------------------

    def list_opportunities(
        self,
        query: str = "",
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        pending_technical_review: bool = False,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Lists or searches qualification opportunities stored in SharePoint.

        Every entry carries a parsed `recordId` and `initiativeName` alongside
        the raw folder name. `sync_opportunity` is the only writer of these
        folders and always names them `{record_id} - {initiative_name}`, so the
        identity is already on the listing — re-deriving it at each call site
        is how two call sites come to disagree.

        `pending_technical_review` keeps only the opportunities that hold a
        business brief and no dossier: the ones a reviewer still has work to do
        on. That asks the folder what is in it rather than consulting a status
        column, so the answer cannot drift from what a human sees in
        SharePoint.
        """
        headers, auth_mode = self.get_graph_headers(delegated_token, context_id=context_id)
        if auth_mode == "unauthenticated" and self._custom_mock_dir is None:
            raise SharePointAuthRequired("Microsoft sign-in required to list SharePoint opportunities.")
        q_norm = query.strip().lower()

        if auth_mode == "mock" or self._custom_mock_dir is not None:
            entries = self._list_mock_opportunities(q_norm)
        else:
            entries = self._list_graph_opportunities(headers, q_norm, limit)
            if entries is None:
                raise RuntimeError(
                    f"SharePoint Graph opportunity listing failed (auth_mode={auth_mode})"
                )

        if pending_technical_review:
            entries = [e for e in entries if e["hasBrief"] and not e["hasDossier"]]
        return entries[:limit]

    def _list_graph_opportunities(
        self, headers: dict[str, str], q_norm: str, limit: int
    ) -> list[dict[str, Any]] | None:
        """Lists opportunity folders over Microsoft Graph, or `None` on failure.

        `None` rather than `[]` so the caller can tell "Graph said there are
        none" from "Graph did not answer" and fall back deliberately.

        SharePoint Online rejects `$expand=children` on a `/children` collection
        endpoint with HTTP 400 (`notSupported`), so we list the folders first
        and fetch each folder's filenames individually — bounded by `limit` so
        a chat turn stays fast.
        """
        try:
            site_id = self.resolve_site_id(headers)
            drive_id = self.resolve_drive_id(headers, site_id=site_id)
            parent_folder = sanitize_path_segment(self.folder_path)
            endpoint = (
                f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                f"/root:/{urllib.parse.quote(parent_folder)}:/children"
            )
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(endpoint, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "SharePoint opportunity listing returned %s.",
                        resp.status_code,
                    )
                    return None

                results: list[dict[str, Any]] = []
                for item in resp.json().get("value", []):
                    name = item.get("name", "")
                    if not name or "folder" not in item:
                        continue
                    if q_norm and q_norm not in name.lower():
                        continue

                    filenames = _expanded_child_filenames(item)
                    if filenames is None and len(results) < limit:
                        filenames = self._fetch_folder_filenames(
                            client, headers, drive_id, parent_folder, name
                        )

                    results.append(
                        self._opportunity_entry(
                            folder_name=name,
                            web_url=item.get("webUrl"),
                            modified=item.get("lastModifiedDateTime"),
                            filenames=filenames or set(),
                            item_id=item.get("id"),
                        )
                    )
                return results
        except Exception as exc:
            logger.warning(
                "SharePoint opportunity listing failed (%s).", exc
            )
            return None

    def _fetch_folder_filenames(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        drive_id: str,
        parent_folder: str,
        folder_name: str,
    ) -> set[str]:
        """Lists the filenames directly inside one opportunity folder."""
        endpoint = (
            f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
            f"/root:/{urllib.parse.quote(parent_folder)}/{urllib.parse.quote(folder_name)}:/children?$select=name"
        )
        try:
            resp = client.get(endpoint, headers=headers)
            if resp.status_code != 200:
                return set()
            return {c.get("name", "") for c in resp.json().get("value", [])}
        except Exception:
            return set()

    def _opportunity_entry(
        self,
        *,
        folder_name: str,
        web_url: str | None,
        filenames: set[str],
        modified: str | None = None,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        """One listing row, with identity and deliverable state resolved."""
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

    def _list_mock_opportunities(self, q_norm: str) -> list[dict[str, Any]]:
        """Lists opportunities from the local SharePoint mock directory."""
        root_dir = (
            self.mock_dir
            / "drives"
            / sanitize_path_segment(self.drive_name)
            / sanitize_path_segment(self.folder_path)
        )
        if not root_dir.is_dir():
            return []

        results = []
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir():
                continue
            if q_norm and q_norm not in child.name.lower():
                continue
            results.append(
                self._opportunity_entry(
                    folder_name=child.name,
                    web_url=f"https://sharepoint.mock/sites/AI-CoE/{urllib.parse.quote(child.name)}",
                    filenames={f.name for f in child.iterdir() if f.is_file()},
                )
            )
        return results

    def load_opportunity(
        self,
        query_or_record_id: str,
        delegated_token: str | None = None,
        context_id: str | None = None,
    ) -> UseCaseRecord | None:
        """Loads a UseCaseRecord from SharePoint by Record ID (e.g. UC-2026-XXXXXX) or Initiative Name."""
        headers, auth_mode = self.get_graph_headers(delegated_token, context_id=context_id)
        target = query_or_record_id.strip()
        if not target:
            return None
        if auth_mode == "unauthenticated" and self._custom_mock_dir is None:
            raise SharePointAuthRequired("Microsoft sign-in required to load from SharePoint.")

        if auth_mode != "mock":
            try:
                site_id = self.resolve_site_id(headers)
                drive_id = self.resolve_drive_id(headers, site_id=site_id)
                parent_folder = sanitize_path_segment(self.folder_path)
                # List folders under Qualification Opportunities to find matching folder
                endpoint = (
                    f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                    f"/root:/{urllib.parse.quote(parent_folder)}:/children"
                )
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(endpoint, headers=headers)
                    if resp.status_code == 200:
                        for item in resp.json().get("value", []):
                            folder_name = item.get("name", "")
                            if target.lower() in folder_name.lower():
                                json_url = (
                                    f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                                    f"/root:/{urllib.parse.quote(parent_folder)}/{urllib.parse.quote(folder_name)}/record.json:/content"
                                )
                                j_resp = client.get(json_url, headers=headers, follow_redirects=True)
                                if j_resp.status_code == 200:
                                    return UseCaseRecord.model_validate_json(j_resp.text)
            except Exception as exc:
                logger.warning("Live SharePoint load failed (%s), checking local mock.", exc)

        # Check local mock directory
        root_dir = (
            self.mock_dir
            / "drives"
            / sanitize_path_segment(self.drive_name)
            / sanitize_path_segment(self.folder_path)
        )
        if root_dir.is_dir():
            for child in root_dir.iterdir():
                if child.is_dir() and target.lower() in child.name.lower():
                    json_file = child / "record.json"
                    if json_file.is_file():
                        return UseCaseRecord.model_validate_json(json_file.read_text(encoding="utf-8"))
        return None

    def load_all_opportunities(
        self,
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        limit: int = 50,
    ) -> list[tuple[UseCaseRecord, dict[str, Any]]]:
        """Loads all qualified opportunities and their `UseCaseRecord`s from SharePoint.

        Returns `(record, listing_entry)` pairs for every folder that holds a
        valid `record.json`. Raises `RuntimeError` if Microsoft Graph is
        configured (`auth_mode != 'mock'`) and unreachable, matching
        `list_opportunities`.
        """
        entries = self.list_opportunities(
            delegated_token=delegated_token,
            context_id=context_id,
            limit=limit,
        )
        if not entries:
            return []

        headers, auth_mode = self.get_graph_headers(delegated_token, context_id=context_id)
        loaded: list[tuple[UseCaseRecord, dict[str, Any]]] = []

        if auth_mode != "mock" and self._custom_mock_dir is None:
            site_id = self.resolve_site_id(headers)
            drive_id = self.resolve_drive_id(headers, site_id=site_id)
            parent_folder = sanitize_path_segment(self.folder_path)
            with httpx.Client(timeout=12.0) as client:
                for entry in entries:
                    folder_name = entry.get("name", "")
                    if not folder_name:
                        continue
                    json_url = (
                        f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                        f"/root:/{urllib.parse.quote(parent_folder)}/{urllib.parse.quote(folder_name)}/record.json:/content"
                    )
                    try:
                        j_resp = client.get(json_url, headers=headers, follow_redirects=True)
                        if j_resp.status_code == 200:
                            rec = UseCaseRecord.model_validate_json(j_resp.text)
                            loaded.append((rec, entry))
                    except Exception as exc:
                        logger.warning("Skipping invalid record.json in %s: %s", folder_name, exc)
            return loaded

        # Mock directory mode
        root_dir = (
            self.mock_dir
            / "drives"
            / sanitize_path_segment(self.drive_name)
            / sanitize_path_segment(self.folder_path)
        )
        for entry in entries:
            folder_name = entry.get("name", "")
            json_file = root_dir / folder_name / "record.json"
            if json_file.is_file():
                try:
                    rec = UseCaseRecord.model_validate_json(json_file.read_text(encoding="utf-8"))
                    loaded.append((rec, entry))
                except Exception as exc:
                    logger.warning("Skipping invalid mock record.json in %s: %s", folder_name, exc)
        return loaded

    def sync_portfolio_report(
        self,
        report_md: str,
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        filename: str = "Portfolio_Prioritization_Report.md",
    ) -> str | None:
        """Uploads `Portfolio_Prioritization_Report.md` to the SharePoint root qualification folder."""
        headers, auth_mode = self.get_graph_headers(delegated_token, context_id=context_id)
        if auth_mode == "unauthenticated" and self._custom_mock_dir is None:
            return None
        safe_filename = sanitize_path_segment(filename)

        if auth_mode != "mock" and self._custom_mock_dir is None:
            try:
                site_id = self.resolve_site_id(headers)
                drive_id = self.resolve_drive_id(headers, site_id=site_id)
                parent_folder = sanitize_path_segment(self.folder_path)
                put_url = (
                    f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                    f"/root:/{urllib.parse.quote(parent_folder)}/{urllib.parse.quote(safe_filename)}:/content"
                )
                put_headers = {
                    **headers,
                    "Content-Type": "text/markdown; charset=utf-8",
                }
                with httpx.Client(timeout=12.0) as client:
                    resp = client.put(put_url, content=report_md.encode("utf-8"), headers=put_headers)
                    if resp.status_code in (200, 201):
                        return resp.json().get("webUrl")
            except Exception as exc:
                logger.warning("Failed to upload portfolio report to live SharePoint: %s", exc)

        # Fallback / mock directory write
        root_dir = (
            self.mock_dir
            / "drives"
            / sanitize_path_segment(self.drive_name)
            / sanitize_path_segment(self.folder_path)
        )
        root_dir.mkdir(parents=True, exist_ok=True)
        report_path = root_dir / safe_filename
        report_path.write_text(report_md, encoding="utf-8")
        return f"https://sharepoint.mock/sites/AI-CoE/{urllib.parse.quote(self.folder_path)}/{urllib.parse.quote(safe_filename)}"


    # -----------------------------------------------------------------------
    # Safe Document Text Extraction (Ported from L400 SharePoint MCP Server)
    # -----------------------------------------------------------------------

    def extract_document_text(
        self,
        drive_id: str,
        item_id: str,
        delegated_token: str | None = None,
    ) -> str:
        """Downloads a document from SharePoint and safely extracts its plain text."""
        headers, auth_mode = self.get_graph_headers(delegated_token)
        if auth_mode == "unauthenticated":
            raise SharePointAuthRequired("A Microsoft user token is required to read documents.")
        if auth_mode == "mock":
            # In mock mode, treat item_id as a relative path or filename inside mock_dir
            candidate = self.mock_dir / sanitize_path_segment(item_id)
            if candidate.is_file():
                return self.extract_text_from_bytes(candidate.read_bytes(), filename=candidate.name)
            return f"Mock file {item_id!r} not found."

        url = (
            f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
            f"/items/{urllib.parse.quote(item_id)}/content"
        )
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            return self.extract_text_from_bytes(resp.content, content_type=content_type)

    @staticmethod
    def extract_text_from_bytes(
        data: bytes,
        *,
        content_type: str = "",
        filename: str = "",
    ) -> str:
        """Extracts text safely from Word (.docx), PowerPoint (.pptx), Excel (.xlsx), or plain text bytes.

        Uses Python's built-in zipfile + ElementTree XML parser with zero external entity expansion.
        Validates zip member paths to prevent Zip Slip / directory traversal.
        """
        is_zip = data.startswith(b"PK\x03\x04")
        if is_zip or any(ext in filename.lower() for ext in (".docx", ".pptx", ".xlsx")):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    paragraphs: list[str] = []
                    for member in zf.namelist():
                        # Security check: block path traversal inside zip archive
                        if ".." in member or member.startswith("/"):
                            continue
                        if member == "word/document.xml" or member.startswith("ppt/slides/slide") or member == "xl/sharedStrings.xml":
                            xml_bytes = zf.read(member)
                            root = ET.fromstring(xml_bytes)  # Safe in Python 3.12+ (external entities disabled by default)
                            texts = [elem.text for elem in root.iter() if elem.text and elem.text.strip()]
                            if texts:
                                paragraphs.append(" ".join(texts))
                    if paragraphs:
                        return "\n\n".join(paragraphs)
            except Exception as exc:
                logger.warning("Office XML extraction warning: %s", exc)

        # Fallback to UTF-8 text decode
        return data.decode("utf-8", errors="replace").strip()


# Singleton instance helper
_CONNECTOR_INSTANCE: SharePointConnector | None = None


def get_sharepoint_connector() -> SharePointConnector:
    """Returns the default SharePointConnector singleton."""
    global _CONNECTOR_INSTANCE
    if _CONNECTOR_INSTANCE is None:
        _CONNECTOR_INSTANCE = SharePointConnector()
    return _CONNECTOR_INSTANCE


def sync_to_optional_sharepoint(
    record: UseCaseRecord,
    *,
    skipped_stages: set[int] | None = None,
    delegated_token: str | None = None,
    context_id: str | None = None,
    pack_name: str = "business",
) -> SharePointSyncResult | None:
    """Non-blocking helper called on final-stage completion to sync to SharePoint.

    `pack_name` decides which deliverable is written. Both land in the same
    record folder, so finishing a technical review adds the dossier next to the
    business brief instead of replacing it.
    """
    try:
        connector = get_sharepoint_connector()
    except Exception as exc:
        logger.warning("SharePoint sync skipped due to error: %s", exc)
        return None
    # Queues for this conversation only when not written as its own user.
    return storage.sync_record(
        connector,
        record,
        skipped_stages=skipped_stages,
        delegated_token=delegated_token,
        context_id=context_id,
        pack_name=pack_name,
    )

