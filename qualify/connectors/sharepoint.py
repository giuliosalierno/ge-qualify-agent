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
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from qualify.export.brief import render_business_brief
from qualify.schema.use_case_record import UseCaseRecord
from qualify.scoring import classify_capability

logger = logging.getLogger(__name__)

# In-memory token vault storing (access_token, expires_at_unix_ts)
_TOKEN_VAULT: dict[str, tuple[str, float]] = {}

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_FOLDER_PATH = "Qualification Opportunities"
DEFAULT_LIST_NAME = "AI Use Case Inventory"


@dataclass
class SharePointSyncResult:
    """Result of syncing a qualification opportunity to SharePoint."""

    success: bool
    record_id: str
    folder_url: str
    brief_url: str
    list_item_id: str | None = None
    auth_mode: str = "mock"
    message: str = ""


def sanitize_path_segment(segment: str) -> str:
    """Sanitizes a folder or file name segment to prevent path traversal and invalid SharePoint characters.

    Strips directory separators and restricts characters to a safe allow-list.
    """
    base = os.path.basename(segment.strip())
    # Replace SharePoint forbidden characters: " * : < > ? / \ |
    cleaned = re.sub(r'["*:<>?/\\|]', "_", base)
    cleaned = re.sub(r"\.\.+", ".", cleaned)
    return cleaned.strip(" .") or "unnamed"


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


def cache_delegated_token(token: str, key: str = "latest", ttl_seconds: int = 3600) -> None:
    """Caches an end-user delegated OAuth 2.0 Bearer token received via MCP."""
    clean = token.strip()
    if clean.lower().startswith("bearer "):
        clean = clean[7:].strip()
    if is_microsoft_graph_token(clean):
        _TOKEN_VAULT[key] = (clean, time.time() + ttl_seconds)
        logger.info("Cached delegated Microsoft Graph user token (key=%s, ttl=%ds)", key, ttl_seconds)


def get_cached_delegated_token(key: str = "latest") -> str | None:
    """Retrieves a non-expired delegated user token from the in-memory vault."""
    entry = _TOKEN_VAULT.get(key)
    if entry is None:
        return None
    token, expires_at = entry
    if time.time() >= expires_at:
        _TOKEN_VAULT.pop(key, None)
        return None
    return token


# Per-user refresh token vault: maps context_id -> refresh_token
_REFRESH_VAULT: dict[str, str] = {}


def save_delegated_refresh_token(
    refresh_token: str,
    access_token: str = "",
    expires_in: int = 3599,
    context_id: str | None = None,
) -> None:
    """Stores a delegated user refresh token and access token per-user (context_id) as well as latest."""
    keys = ["latest"]
    if context_id and context_id != "latest":
        keys.insert(0, context_id)

    for k in keys:
        if refresh_token:
            _REFRESH_VAULT[k] = refresh_token
        if access_token:
            cache_delegated_token(access_token, key=k, ttl_seconds=max(60, expires_in - 60))

    if refresh_token:
        os.environ["MS_GRAPH_REFRESH_TOKEN"] = refresh_token

    try:
        token_dir = Path(".data/sharepoint_tokens")
        token_dir.mkdir(parents=True, exist_ok=True)
        for k in keys:
            safe_k = re.sub(r"[^a-zA-Z0-9_-]", "_", k)
            (token_dir / f"{safe_k}.json").write_text(
                json.dumps({"refresh_token": refresh_token, "access_token": access_token}, indent=2),
                encoding="utf-8",
            )
    except Exception as exc:
        logger.debug("Could not write token file: %s", exc)


def load_delegated_refresh_token(context_id: str | None = None) -> str:
    """Loads a persisted delegated user refresh token for a specific user session (context_id) or latest."""
    keys = []
    if context_id and context_id != "latest":
        keys.append(context_id)
    keys.append("latest")

    for k in keys:
        if k in _REFRESH_VAULT and _REFRESH_VAULT[k]:
            return _REFRESH_VAULT[k]
        try:
            safe_k = re.sub(r"[^a-zA-Z0-9_-]", "_", k)
            token_file = Path(f".data/sharepoint_tokens/{safe_k}.json")
            if token_file.exists():
                data = json.loads(token_file.read_text(encoding="utf-8"))
                tok = data.get("refresh_token", "").strip()
                if tok:
                    _REFRESH_VAULT[k] = tok
                    return tok
        except Exception:
            pass

    env_tok = os.environ.get("MS_GRAPH_REFRESH_TOKEN", "").strip()
    if env_tok:
        return env_tok
    return ""


# Pending records awaiting user authentication (context_id -> (UseCaseRecord, skipped_stages))
_PENDING_RECORDS: dict[str, tuple[UseCaseRecord, set[int]]] = {}
# Synced results after authentication (context_id -> dict with syncedUrl, recordId, title)
_SYNCED_RESULTS: dict[str, dict[str, Any]] = {}


def get_synced_result(context_id: str | None = None) -> dict[str, Any] | None:
    """Returns the last synced SharePoint result for a user session (or latest)."""
    if context_id and context_id in _SYNCED_RESULTS:
        return _SYNCED_RESULTS[context_id]
    return _SYNCED_RESULTS.get("latest")


def auto_sync_pending_records(access_token: str, context_id: str = "latest") -> SharePointSyncResult | None:
    """Automatically syncs any pending UseCaseRecord for the session immediately after user sign-in."""
    pending = _PENDING_RECORDS.pop(context_id, None) or _PENDING_RECORDS.pop("latest", None)
    if not pending:
        return None
    pending_rec, pending_skipped = pending
    logger.info("Auto-syncing pending opportunity %s to SharePoint after user sign-in", pending_rec.meta.record_id)
    try:
        res = sync_to_optional_sharepoint(
            pending_rec,
            skipped_stages=pending_skipped,
            delegated_token=access_token,
            context_id=context_id,
        )
        if res and res.auth_mode == "delegated":
            info = {
                "syncedUrl": res.folder_url,
                "briefUrl": res.brief_url,
                "recordId": res.record_id,
                "title": pending_rec.meta.initiative_name or res.record_id,
            }
            _SYNCED_RESULTS[context_id] = info
            _SYNCED_RESULTS["latest"] = info
        return res
    except Exception as exc:
        logger.warning("Auto-sync after sign-in failed: %s", exc)
        return None


def start_device_code_flow_for_session(context_id: str = "latest") -> dict[str, Any] | None:
    """Initiates a Microsoft Device Code OAuth 2.0 flow for a specific user session (context_id) and polls in background."""
    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MS_GRAPH_CLIENT_SECRET", "").strip()
    if not tenant_id or not client_id:
        return None

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/devicecode",
                data={
                    "client_id": client_id,
                    "scope": "https://graph.microsoft.com/Sites.ReadWrite.All offline_access",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Device code request failed: %s", exc)
        return None

    user_code = data.get("user_code", "")
    device_code = data.get("device_code", "")
    verification_uri = data.get("verification_uri", "https://login.microsoft.com/device")
    interval = int(data.get("interval", 5))

    def _poll_worker() -> None:
        token_url = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token"
        start_time = time.time()
        while time.time() - start_time < 600:
            time.sleep(interval)
            try:
                with httpx.Client(timeout=8.0) as client:
                    # Microsoft Device Code flow is a public client flow; do NOT send client_secret first
                    payload = {
                        "client_id": client_id,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                    }
                    r = client.post(token_url, data=payload)
                    if r.status_code in (400, 401) and client_secret and "authorization_pending" not in r.text:
                        # Retry with client_secret if app registration requires confidential client auth
                        payload["client_secret"] = client_secret
                        r = client.post(token_url, data=payload)
                    if r.status_code == 200:
                        res = r.json()
                        access_token = res.get("access_token", "")
                        refresh_token = res.get("refresh_token", "")
                        expires_in = int(res.get("expires_in", 3599))
                        save_delegated_refresh_token(
                            refresh_token,
                            access_token,
                            expires_in,
                            context_id=context_id,
                        )
                        logger.info("Successfully authenticated Microsoft user for session context_id=%s", context_id)
                        auto_sync_pending_records(access_token, context_id=context_id)
                        return
                    try:
                        err = r.json().get("error", "")
                    except Exception:
                        err = ""
                    if err not in ("authorization_pending", "slow_down", "invalid_client"):
                        logger.warning("Device code poll stopped on error %s: %s", err, r.text[:200])
                        return
            except Exception as exc:
                logger.debug("Device code poll exception: %s", exc)

    threading.Thread(target=_poll_worker, daemon=True).start()
    return {
        "user_code": user_code,
        "verification_uri": verification_uri,
        "message": data.get("message", f"Open {verification_uri} and enter code {user_code}"),
    }



class SharePointConnector:
    """Client for reading and writing qualification opportunities in SharePoint Online."""

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
        """Resolves Microsoft Graph Authorization headers using Dual-Layer OAuth 2.0.

        Returns:
            (headers_dict, auth_mode) where auth_mode is 'delegated', 'client_credentials', or 'mock'.
        """
        # Force mock mode if explicitly requested
        if os.environ.get("SHAREPOINT_MOCK") == "1":
            return {"Authorization": "Bearer mock_graph_token", "Accept": "application/json"}, "mock"

        # Layer 1: Explicit or cached Delegated User Identity Token (per-user context_id first, then latest)
        token_candidate = (
            delegated_token
            or (get_cached_delegated_token(context_id) if context_id else None)
            or get_cached_delegated_token("latest")
        )
        if token_candidate:
            clean = token_candidate.strip()
            if clean.lower().startswith("bearer "):
                clean = clean[7:].strip()
            if is_microsoft_graph_token(clean):
                cache_delegated_token(clean, key=context_id or "latest")
                return {
                    "Authorization": f"Bearer {clean}",
                    "Accept": "application/json",
                }, "delegated"

        # Layer 1b: Refresh Token exchange for Delegated User Identity (per-user context_id first)
        refresh_token = load_delegated_refresh_token(context_id)
        if refresh_token and self.tenant_id and self.client_id:
            token_url = f"https://login.microsoftonline.com/{urllib.parse.quote(self.tenant_id)}/oauth2/v2.0/token"
            try:
                with httpx.Client(timeout=8.0) as client:
                    payload = {
                        "client_id": self.client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": "https://graph.microsoft.com/Sites.ReadWrite.All offline_access",
                    }
                    # Try public client refresh first (for Device Code tokens), then confidential client if needed
                    resp = client.post(
                        token_url,
                        data=payload,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    if resp.status_code in (400, 401) and self.client_secret:
                        payload["client_secret"] = self.client_secret
                        resp = client.post(
                            token_url,
                            data=payload,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                        )
                    if resp.status_code == 200:
                        data = resp.json()
                        access_token = data["access_token"]
                        new_rt = data.get("refresh_token", refresh_token)
                        expires_in = int(data.get("expires_in", 3599))
                        save_delegated_refresh_token(new_rt, access_token, expires_in, context_id=context_id)
                        return {
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json",
                        }, "delegated"
            except Exception as exc:
                logger.warning("Microsoft Entra refresh_token flow failed (%s), falling back to client_credentials.", type(exc).__name__)

        # Layer 2: Application Client Credentials OAuth 2.0 flow
        if self.tenant_id and self.client_id and self.client_secret:
            cached_app = get_cached_delegated_token("app_client_credentials")
            if cached_app:
                return {
                    "Authorization": f"Bearer {cached_app}",
                    "Accept": "application/json",
                }, "client_credentials"

            token_url = f"https://login.microsoftonline.com/{urllib.parse.quote(self.tenant_id)}/oauth2/v2.0/token"
            try:
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(
                        token_url,
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "scope": "https://graph.microsoft.com/.default",
                            "grant_type": "client_credentials",
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    access_token = data["access_token"]
                    expires_in = int(data.get("expires_in", 3599))
                    cache_delegated_token(access_token, key="app_client_credentials", ttl_seconds=max(60, expires_in - 60))
                    return {
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    }, "client_credentials"
            except Exception as exc:
                logger.warning("Microsoft Entra client_credentials flow failed (%s), falling back to mock mode.", type(exc).__name__)

        # Layer 3: Local Mock / Offline Mode
        return {"Authorization": "Bearer mock_graph_token", "Accept": "application/json"}, "mock"

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
    ) -> SharePointSyncResult:
        """Synchronizes a UseCaseRecord and Business Value Brief to SharePoint."""
        headers, auth_mode = self.get_graph_headers(delegated_token, context_id=context_id)
        record_id = sanitize_path_segment(record.meta.record_id or "UC-UNKNOWN")
        init_name = sanitize_path_segment(record.meta.initiative_name or "Untitled Initiative")
        folder_name = f"{record_id} - {init_name}"

        brief_md = render_business_brief(record, skipped_stages=skipped_stages)
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
            return self._sync_mock(record_id, folder_name, brief_md, record_json, list_fields, auth_mode=auth_mode)

        try:
            site_id = self.resolve_site_id(headers)
            drive_id = self.resolve_drive_id(headers, site_id=site_id)
            parent_folder = sanitize_path_segment(self.folder_path)
            rel_folder_path = f"{parent_folder}/{folder_name}"

            with httpx.Client(timeout=12.0) as client:
                # 1. Upload Business_Value_Brief.md
                brief_endpoint = (
                    f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                    f"/root:/{urllib.parse.quote(rel_folder_path)}/Business_Value_Brief.md:/content"
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
            return self._sync_mock(record_id, folder_name, brief_md, record_json, list_fields)

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
    ) -> SharePointSyncResult:
        """Writes SharePoint folder & list state to local filesystem mock directory."""
        base_folder = self.mock_dir / "drives" / sanitize_path_segment(self.drive_name) / sanitize_path_segment(self.folder_path) / folder_name
        base_folder.mkdir(parents=True, exist_ok=True)

        brief_path = base_folder / "Business_Value_Brief.md"
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
        brief_url = f"{folder_url}/Business_Value_Brief.md"
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

    def list_opportunities(self, query: str = "", delegated_token: str | None = None) -> list[dict[str, Any]]:
        """Lists or searches qualification opportunities stored in SharePoint."""
        headers, auth_mode = self.get_graph_headers(delegated_token)
        q_norm = query.strip().lower()

        if auth_mode == "mock":
            return self._list_mock_opportunities(q_norm)

        try:
            site_id = self.resolve_site_id(headers)
            drive_id = self.resolve_drive_id(headers, site_id=site_id)
            parent_folder = sanitize_path_segment(self.folder_path)
            endpoint = (
                f"{GRAPH_BASE_URL}/drives/{urllib.parse.quote(drive_id)}"
                f"/root:/{urllib.parse.quote(parent_folder)}:/children"
            )
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(endpoint, headers=headers)
                if resp.status_code != 200:
                    return self._list_mock_opportunities(q_norm)
                children = resp.json().get("value", [])
                results = []
                for item in children:
                    name = item.get("name", "")
                    if not q_norm or q_norm in name.lower():
                        results.append(
                            {
                                "id": item.get("id"),
                                "name": name,
                                "webUrl": item.get("webUrl"),
                                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
                            }
                        )
                return results
        except Exception:
            return self._list_mock_opportunities(q_norm)

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
            if child.is_dir():
                if not q_norm or q_norm in child.name.lower():
                    results.append(
                        {
                            "id": child.name,
                            "name": child.name,
                            "webUrl": f"https://sharepoint.mock/sites/AI-CoE/{urllib.parse.quote(child.name)}",
                        }
                    )
        return results

    def load_opportunity(
        self, query_or_record_id: str, delegated_token: str | None = None
    ) -> UseCaseRecord | None:
        """Loads a UseCaseRecord from SharePoint by Record ID (e.g. UC-2026-XXXXXX) or Initiative Name."""
        headers, auth_mode = self.get_graph_headers(delegated_token)
        target = query_or_record_id.strip()
        if not target:
            return None

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
) -> SharePointSyncResult | None:
    """Non-blocking helper called on Stage 4 completion to sync the opportunity to SharePoint."""
    try:
        connector = get_sharepoint_connector()
        res = connector.sync_opportunity(
            record,
            skipped_stages=skipped_stages,
            delegated_token=delegated_token,
            context_id=context_id,
        )
        if res:
            cid = context_id or "latest"
            if res.auth_mode != "delegated":
                _PENDING_RECORDS[cid] = (record, set(skipped_stages or set()))
                _PENDING_RECORDS["latest"] = (record, set(skipped_stages or set()))
            else:
                info = {
                    "syncedUrl": res.folder_url,
                    "briefUrl": res.brief_url,
                    "recordId": res.record_id,
                    "title": record.meta.initiative_name or res.record_id,
                }
                _SYNCED_RESULTS[cid] = info
                _SYNCED_RESULTS["latest"] = info
                _PENDING_RECORDS.pop(cid, None)
        return res
    except Exception as exc:
        logger.warning("SharePoint sync skipped due to error: %s", exc)
        return None

