"""MCP (Model Context Protocol) HTTP Server for SharePoint Integration.

Exposes:
- `POST /mcp`: Streamable HTTP JSON-RPC 2.0 endpoint for Gemini Enterprise MCP registration.
  Captures delegated `Authorization: Bearer <token>` headers and forwards them to `SharePointConnector`.
- `GET /auth`: OAuth 2.0 authorization redirect endpoint for Gemini Enterprise Federated Connector registration.
- `POST /token`: OAuth 2.0 token exchange endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from typing import Any
import urllib.parse

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from qualify.connectors.sharepoint import (
    cache_delegated_token,
    get_sharepoint_connector,
    is_microsoft_graph_token,
    save_delegated_refresh_token,
)

logger = logging.getLogger(__name__)

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_qualification_opportunities",
        "description": "Search qualified AI use case opportunities stored in SharePoint by keyword, Record ID (UC-2026-XXXXXX), or department.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search filter (e.g. 'AP Invoice', 'UC-2026-481209', or empty string to list all)",
                }
            },
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
    {
        "name": "load_qualification_opportunity",
        "description": "Load a specific UseCaseRecord JSON payload from SharePoint by Record ID (UC-2026-XXXXXX) or Initiative Name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recordIdOrName": {
                    "type": "string",
                    "description": "The Record ID (e.g. UC-2026-481209) or Initiative Name to load from SharePoint.",
                }
            },
            "required": ["recordIdOrName"],
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
    {
        "name": "read_sharepoint_document",
        "description": "Extract readable plain text from a SharePoint document (.docx, .pptx, .xlsx, .md, .txt) to pre-populate qualification answers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "driveId": {
                    "type": "string",
                    "description": "SharePoint Drive GUID or 'Documents'",
                },
                "itemId": {
                    "type": "string",
                    "description": "File Item GUID or filename",
                },
            },
            "required": ["itemId"],
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
    {
        "name": "query_sharepoint_sites_lookup",
        "description": "Read-only lookup to query SharePoint site metadata (L400 compatible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional search filter"}
            },
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
    {
        "name": "query_document_libraries_lookup",
        "description": "Read-only lookup to list document library drives on a SharePoint site (L400 compatible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "siteId": {"type": "string", "description": "Optional site ID or name"}
            },
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
    {
        "name": "query_library_items_lookup",
        "description": "Read-only lookup to list files and folders inside a SharePoint library (L400 compatible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "driveId": {"type": "string", "description": "Optional drive ID or 'Documents'"},
                "folderId": {"type": "string", "description": "Optional folder ID or path"},
            },
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
    {
        "name": "query_file_content_lookup",
        "description": "Read-only lookup to retrieve document content text from SharePoint (L400 compatible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "driveId": {"type": "string", "description": "Optional drive ID"},
                "itemId": {"type": "string", "description": "File item ID or name"},
            },
        },
        "annotations": {
            "destructiveHint": False,
            "readOnlyHint": True,
        },
    },
]


async def handle_mcp_request(request: Request) -> Response:
    """Handles MCP JSON-RPC 2.0 requests (`initialize`, `tools/list`, `tools/call`)."""
    # Capture and cache any delegated OAuth 2.0 Bearer token sent by Gemini Enterprise
    candidate_header = (
        request.headers.get("x-ms-graph-token")
        or request.headers.get("x-forwarded-access-token")
        or request.headers.get("authorization")
    )
    delegated_token: str | None = None
    if candidate_header:
        raw_token = candidate_header[7:].strip() if candidate_header.lower().startswith("bearer ") else candidate_header.strip()
        if is_microsoft_graph_token(raw_token):
            delegated_token = raw_token
            cache_delegated_token(raw_token)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )

    rpc_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params") or {}

    # 1. initialize handshake
    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "ge-qualify-sharepoint-mcp",
                        "version": "1.0.0",
                    },
                },
            }
        )

    # 2. notifications/initialized (no response body required for notifications)
    if method.startswith("notifications/"):
        return Response(status_code=204)

    # 3. tools/list
    if method == "tools/list":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"tools": MCP_TOOLS},
            }
        )

    # 4. tools/call
    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments") or {}
        connector = get_sharepoint_connector()

        try:
            if tool_name in ("search_qualification_opportunities", "query_library_items_lookup"):
                query = args.get("query") or args.get("folderId") or ""
                items = connector.list_opportunities(query=query, delegated_token=delegated_token)
                result_text = json.dumps({"opportunities": items}, indent=2)
            elif tool_name == "load_qualification_opportunity":
                target = args.get("recordIdOrName") or args.get("query") or ""
                record = connector.load_opportunity(target, delegated_token=delegated_token)
                if record is None:
                    result_text = json.dumps({"error": f"No opportunity found matching {target!r}."})
                else:
                    result_text = record.model_dump_json(indent=2)
            elif tool_name in ("read_sharepoint_document", "query_file_content_lookup"):
                drive_id = args.get("driveId") or "Documents"
                item_id = args.get("itemId") or ""
                text = connector.extract_document_text(drive_id, item_id, delegated_token=delegated_token)
                result_text = json.dumps({"content": text}, indent=2)
            elif tool_name == "query_sharepoint_sites_lookup":
                headers, auth_mode = connector.get_graph_headers(delegated_token)
                site_id = connector.resolve_site_id(headers, args.get("query"))
                result_text = json.dumps({"siteId": site_id, "authMode": auth_mode}, indent=2)
            elif tool_name == "query_document_libraries_lookup":
                headers, auth_mode = connector.get_graph_headers(delegated_token)
                site_id = connector.resolve_site_id(headers, args.get("siteId"))
                drive_id = connector.resolve_drive_id(headers, site_id=site_id)
                result_text = json.dumps({"siteId": site_id, "driveId": drive_id, "authMode": auth_mode}, indent=2)
            else:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                    },
                    status_code=404,
                )

            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                    },
                }
            )
        except Exception as exc:
            logger.warning("MCP tool %s execution error: %s", tool_name, exc)
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }
            )

    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        },
        status_code=404,
    )


async def handle_oauth_auth(request: Request) -> Response:
    """OAuth 2.0 Authorization Endpoint (`/auth`) for browser login and Gemini Enterprise connector registration."""
    ge_redirect_uri = request.query_params.get("redirect_uri", "")
    ge_state = request.query_params.get("state", "")

    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
    base_url = os.environ.get("AGENT_URL", f"http://{request.url.netloc}").rstrip("/")
    callback_uri = f"{base_url}/auth/callback"

    if tenant_id and client_id:
        # Encode Gemini Enterprise redirect_uri & state into state payload
        state_payload = json.dumps({"redirect_uri": ge_redirect_uri, "state": ge_state})
        encoded_state = base64.urlsafe_b64encode(state_payload.encode("utf-8")).decode("ascii")
        auth_url = (
            f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/authorize?"
            + urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "response_type": "code",
                    "redirect_uri": callback_uri,
                    "response_mode": "query",
                    "scope": "https://graph.microsoft.com/Sites.ReadWrite.All offline_access",
                    "state": encoded_state,
                }
            )
        )
        return RedirectResponse(url=auth_url, status_code=302)

    if not ge_redirect_uri:
        return JSONResponse({"error": "Missing redirect_uri and MS_GRAPH_CLIENT_ID not configured"}, status_code=400)

    code = secrets.token_urlsafe(24)
    sep = "&" if "?" in ge_redirect_uri else "?"
    target_url = f"{ge_redirect_uri}{sep}code={code}&state={ge_state}"
    return RedirectResponse(url=target_url, status_code=302)


async def handle_oauth_callback(request: Request) -> Response:
    """OAuth 2.0 Callback Endpoint (`/auth/callback`) that receives Microsoft Entra auth code and saves refresh token."""
    code = request.query_params.get("code", "")
    state_str = request.query_params.get("state", "")
    error = request.query_params.get("error", "")
    error_desc = request.query_params.get("error_description", "")

    if error:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2rem;'><h2>Microsoft Sign-In Error</h2><p><code>{error}</code>: {error_desc}</p></body></html>",
            status_code=400,
        )

    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MS_GRAPH_CLIENT_SECRET", "").strip()
    base_url = os.environ.get("AGENT_URL", f"http://{request.url.netloc}").rstrip("/")
    callback_uri = f"{base_url}/auth/callback"

    access_token = ""
    refresh_token = ""
    expires_in = 3599

    if code and tenant_id and client_id:
        token_url = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token"
        try:
            with httpx.Client(timeout=10.0) as client:
                payload = {
                    "client_id": client_id,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback_uri,
                    "scope": "https://graph.microsoft.com/Sites.ReadWrite.All offline_access",
                }
                if client_secret:
                    payload["client_secret"] = client_secret
                resp = client.post(
                    token_url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                data = resp.json()
                access_token = data.get("access_token", "")
                refresh_token = data.get("refresh_token", "")
                expires_in = int(data.get("expires_in", 3599))
                save_delegated_refresh_token(refresh_token, access_token, expires_in)
                logger.info("Successfully exchanged Microsoft Entra authorization code for Delegated Access & Refresh Token.")
        except Exception as exc:
            logger.error("Failed to exchange Microsoft Entra authorization code: %s", exc)
            return HTMLResponse(
                f"<html><body style='font-family:sans-serif;padding:2rem;'><h2>Token Exchange Failed</h2><p>{exc}</p></body></html>",
                status_code=500,
            )

    # Decode state to check if initiated by Gemini Enterprise
    ge_redirect_uri = ""
    ge_state = ""
    if state_str:
        try:
            padded = state_str + "=" * (-len(state_str) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            ge_redirect_uri = decoded.get("redirect_uri", "")
            ge_state = decoded.get("state", "")
        except Exception:
            pass

    if ge_redirect_uri:
        ephemeral_code = secrets.token_urlsafe(24)
        sep = "&" if "?" in ge_redirect_uri else "?"
        target_url = f"{ge_redirect_uri}{sep}code={ephemeral_code}&state={ge_state}"
        return RedirectResponse(url=target_url, status_code=302)

    site_url = os.environ.get("SHAREPOINT_INSTANCE_URL", "https://zd8vn.sharepoint.com/")
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>SharePoint Connected</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #0f172a; padding: 3rem 1.5rem; max-width: 680px; margin: 0 auto; }}
    .card {{ background: white; border-radius: 12px; padding: 2rem; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border: 1px solid #e2e8f0; }}
    h1 {{ color: #16a34a; margin-top: 0; font-size: 1.5rem; }}
    code {{ background: #f1f5f9; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.85rem; word-break: break-all; display: block; margin-top: 0.5rem; padding: 0.75rem; }}
    .btn {{ display: inline-block; margin-top: 1.25rem; background: #2563eb; color: white; text-decoration: none; padding: 0.6rem 1.2rem; border-radius: 6px; font-weight: 500; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>✅ Microsoft SharePoint Online Connected!</h1>
    <p>Your Delegated User Identity &amp; Refresh Token are now active on <strong>GE Use Case Qualification Agent</strong>.</p>
    <p>Both <strong>Stage 4 Qualification Submissions</strong> and <strong>MCP Tool Calls</strong> will now read and write directly to:</p>
    <p><a href="{site_url}" target="_blank">{site_url}</a></p>
    <hr style="border:0;border-top:1px solid #e2e8f0;margin:1.5rem 0;">
    <p style="font-size:0.9rem;color:#475569;"><strong>Optional (Permanent .env Refresh Token):</strong> Copy this <code>MS_GRAPH_REFRESH_TOKEN</code> to persist across future container rebuilds:</p>
    <code>MS_GRAPH_REFRESH_TOKEN={refresh_token}</code>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


def _fetch_cloud_run_oidc_token(audience: str) -> str | None:
    """Fetches a Google OIDC Identity Token from the Cloud Run metadata server so Gemini Enterprise can pass Cloud Run GFE IAM checks."""
    try:
        url = f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={urllib.parse.quote(audience)}"
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url, headers={"Metadata-Flavor": "Google"})
            if resp.status_code == 200 and resp.text.strip():
                return resp.text.strip()
    except Exception:
        pass
    return None


async def handle_oauth_token(request: Request) -> Response:
    """OAuth 2.0 Token Exchange Endpoint (`/token`) for Gemini Enterprise Connector registration.

    Returns a Cloud Run-compatible Google OIDC transport token as `access_token` so Gemini Enterprise's
    subsequent `Authorization: Bearer <token>` calls to `POST /mcp` pass Google Cloud Run Frontend (GFE)
    with 200 OK, while our server executes all SharePoint Graph API queries using the user's personal
    Microsoft Delegated Access Token & Refresh Token captured during `/auth/callback`.
    """
    connector = get_sharepoint_connector()
    headers, auth_mode = connector.get_graph_headers()
    auth_val = headers.get("Authorization", "Bearer mock_graph_token")
    ms_token = auth_val[7:] if auth_val.startswith("Bearer ") else auth_val

    base_url = os.environ.get("AGENT_URL", f"http://{request.url.netloc}").rstrip("/")
    transport_token = _fetch_cloud_run_oidc_token(base_url) or ms_token

    return JSONResponse(
        {
            "access_token": transport_token,
            "token_type": "Bearer",
            "expires_in": 3500,
            "refresh_token": os.environ.get("MS_GRAPH_REFRESH_TOKEN") or f"refresh_{secrets.token_urlsafe(16)}",
            "auth_mode": auth_mode,
        }
    )
