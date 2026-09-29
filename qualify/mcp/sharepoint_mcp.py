"""MCP (Model Context Protocol) HTTP Server and sign-in endpoints for SharePoint.

Exposes:
- `POST /mcp`: Streamable HTTP JSON-RPC 2.0 endpoint. A delegated
  `Authorization: Bearer <token>` is used for that request only and is never
  vaulted: an inbound header proves nothing about which conversation it
  belongs to.
- `GET /auth?t=<signed link>`: sign-in page for one conversation.
- `GET /auth/status?t=<signed link>`: sign-in status for that page's poller.
- `GET /auth/callback`: Microsoft redirect target; verifies the signed `state`.

The former `/token` and `/auth/exchange` endpoints were removed. `/token`
answered unauthenticated callers with live user tokens, and Gemini Enterprise
never called it (it does not forward delegated tokens over A2A).
"""

from __future__ import annotations

import html
import json
import logging
import os
from typing import Any
import urllib.parse

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from qualify.connectors import oauth_state
from qualify.connectors.sharepoint import (
    DELEGATED_GRAPH_SCOPE,
    auto_sync_pending_records,
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
    # A delegated token sent with this request is used for this request only.
    # It is deliberately NOT vaulted: doing so let any caller plant a token
    # that later requests would silently reuse.
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


def _page(title: str, body_html: str, status_code: int = 200) -> HTMLResponse:
    """Minimal HTML shell. Every dynamic value in `body_html` must already be escaped."""
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #0f172a; padding: 2.5rem 1.25rem; max-width: 640px; margin: 0 auto; }}
  .card {{ background: white; border-radius: 12px; padding: 2rem; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border: 1px solid #e2e8f0; }}
</style></head>
<body><div class="card">{body_html}</div></body></html>""",
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def _invalid_link_page() -> HTMLResponse:
    return _page(
        "Sign-in link expired",
        "<h2>This sign-in link is invalid or has expired</h2>"
        "<p>Go back to Gemini Enterprise and ask the agent to <strong>connect SharePoint</strong> "
        "to get a fresh link.</p>",
        status_code=400,
    )


def _base_url(request: Request) -> str:
    return os.environ.get("AGENT_URL", f"https://{request.url.netloc}").rstrip("/")


async def handle_oauth_auth(request: Request) -> Response:
    """Sign-in page (`GET /auth?t=<signed link>`).

    The conversation is taken only from the signed link the agent rendered in
    chat. An unsigned `context_id` is ignored, so nobody can attach their own
    Microsoft account to someone else's conversation.
    """
    link_token = request.query_params.get("t", "")
    context_id = oauth_state.verify(link_token, "link")
    if not context_id:
        return _invalid_link_page()

    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
    if not (tenant_id and client_id):
        return _page(
            "SharePoint not configured",
            "<h2>SharePoint is not configured on this agent</h2>"
            "<p>MS_GRAPH_TENANT_ID and MS_GRAPH_CLIENT_ID must be set.</p>",
            status_code=503,
        )

    base_url = _base_url(request)
    callback_uri = f"{base_url}/auth/callback"
    web_oauth_ready = os.environ.get("WEB_OAUTH_CALLBACK") == "1"

    ms_web_auth_url = (
        f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": callback_uri,
                "response_mode": "query",
                "scope": DELEGATED_GRAPH_SCOPE,
                "state": oauth_state.issue(context_id, "state", oauth_state.STATE_TTL_SECONDS),
                "prompt": "select_account",
            }
        )
    )

    if web_oauth_ready:
        method_html = f"""
    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:1.25rem;">
      <h3 style="margin-top:0;color:#1e40af;font-size:1.05rem;">⚡ Recommended: one-click sign-in</h3>
      <p style="font-size:0.9rem;color:#334155;">Sign in with Microsoft and you will be returned here automatically. Nothing to copy.</p>
      <p><a style="display:inline-block;background:#2563eb;color:white;text-decoration:none;padding:0.75rem 1.4rem;border-radius:8px;font-weight:600;" href="{html.escape(ms_web_auth_url, quote=True)}">Sign in with Microsoft ↗</a></p>
    </div>"""
    else:
        method_html = f"""
    <div style="background:#fef3c7;border:1px solid #fde68a;border-radius:10px;padding:1.25rem;">
      <h3 style="margin-top:0;color:#92400e;font-size:1.05rem;">One-click sign-in is not enabled yet</h3>
      <p style="font-size:0.9rem;color:#334155;">It needs this redirect URI registered on the Azure app, then <code>WEB_OAUTH_CALLBACK=1</code> on the service:</p>
      <p><code style="font-size:0.82rem;word-break:break-all;">{html.escape(callback_uri)}</code></p>
    </div>"""

    # `t` is embedded as JSON so it cannot break out of the script context.
    status_js = json.dumps(link_token)
    body = f"""
    <h2 style="margin-top:0;text-align:center;">🔐 Connect Microsoft SharePoint Online</h2>
    <div id="methods">{method_html}</div>
    <p id="poll-status" style="text-align:center;color:#64748b;font-size:0.85rem;">⏳ Waiting for sign-in completion...</p>
    <script>
      const linkToken = {status_js};
      const timer = setInterval(async () => {{
        try {{
          const resp = await fetch(`/auth/status?t=${{encodeURIComponent(linkToken)}}`);
          if (!resp.ok) return;
          const data = await resp.json();
          if (data.authenticated) {{
            clearInterval(timer);
            document.getElementById("methods").remove();
            document.getElementById("poll-status").textContent =
              "✅ Microsoft SharePoint connected. You can close this window and return to Gemini Enterprise.";
          }}
        }} catch (e) {{}}
      }}, 2000);
    </script>"""
    return _page("Sign in to Microsoft SharePoint", body)


async def handle_oauth_status(request: Request) -> Response:
    """Sign-in status for the page's poller. Requires the same signed link as `/auth`."""
    context_id = oauth_state.verify(request.query_params.get("t", ""), "link")
    if not context_id:
        return JSONResponse({"error": "invalid or expired link"}, status_code=400)

    from qualify.connectors.sharepoint import has_user_session  # noqa: PLC0415

    return JSONResponse(
        {"authenticated": has_user_session(context_id)},
        headers={"Cache-Control": "no-store"},
    )


async def handle_oauth_callback(request: Request) -> Response:
    """OAuth 2.0 redirect target (`/auth/callback`).

    The conversation comes only from the signed, short-lived `state` we issued
    on `/auth`. Tokens are vaulted in memory for that conversation and are
    never rendered, logged or returned.
    """
    error = request.query_params.get("error", "")
    if error:
        return _page(
            "Microsoft sign-in error",
            "<h2>Microsoft sign-in error</h2>"
            f"<p><code>{html.escape(error)}</code>: "
            f"{html.escape(request.query_params.get('error_description', ''))}</p>",
            status_code=400,
        )

    context_id = oauth_state.verify(request.query_params.get("state", ""), "state")
    code = request.query_params.get("code", "")
    if not context_id or not code:
        return _invalid_link_page()

    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MS_GRAPH_CLIENT_SECRET", "").strip()
    if not (tenant_id and client_id):
        return _page("SharePoint not configured", "<h2>SharePoint is not configured on this agent</h2>", 503)

    payload = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{_base_url(request)}/auth/callback",
        "scope": DELEGATED_GRAPH_SCOPE,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token",
                data=payload,
            )
        if resp.status_code != 200:
            # Log only the Entra error code; the body can echo request details.
            try:
                err_code = str(resp.json().get("error", ""))
            except Exception:
                err_code = ""
            logger.warning("Entra code exchange failed (%s %s)", resp.status_code, err_code)
            return _page(
                "Token exchange failed",
                "<h2>Microsoft sign-in could not be completed</h2>"
                f"<p>Entra returned <code>{html.escape(err_code or str(resp.status_code))}</code>. "
                "Go back to Gemini Enterprise and request a new sign-in link.</p>",
                status_code=502,
            )
        data = resp.json()
    except Exception as exc:
        logger.error("Entra code exchange error: %s", type(exc).__name__)
        return _page("Token exchange failed", "<h2>Microsoft sign-in could not be completed</h2>", 502)

    access_token = data.get("access_token", "")
    save_delegated_refresh_token(
        data.get("refresh_token", ""),
        access_token,
        int(data.get("expires_in", 3599)),
        context_id=context_id,
    )
    logger.info("Microsoft sign-in completed for context %s", context_id)

    # Write anything queued while the user was unauthenticated. GE never polls
    # the task after an out-of-band sign-in, so without this the record would
    # sit waiting until the user asks a second time.
    try:
        if auto_sync_pending_records(access_token, context_id=context_id):
            logger.info("Auto-synced pending record for context %s", context_id)
    except Exception as exc:
        logger.warning("Post-login auto-sync failed for %s: %s", context_id, exc)

    return _page(
        "SharePoint connected",
        "<h2 style='color:#16a34a;margin-top:0;'>✅ Microsoft SharePoint connected</h2>"
        "<p>You can close this window and return to Gemini Enterprise. "
        "Type <strong>save to sharepoint</strong> (or finish the interview) to save your opportunity.</p>",
    )
