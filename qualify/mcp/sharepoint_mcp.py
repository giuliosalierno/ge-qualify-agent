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
    auto_sync_pending_records,
    cache_delegated_token,
    get_sharepoint_connector,
    is_microsoft_graph_token,
    save_delegated_refresh_token,
)

logger = logging.getLogger(__name__)

# Delegated Microsoft Graph scope. `offline_access` is mandatory for Entra to return a refresh
# token; without it the user would have to re-consent roughly every hour.
DEFAULT_GRAPH_SCOPE = "https://graph.microsoft.com/Sites.ReadWrite.All offline_access"

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
    """OAuth 2.0 Authorization Endpoint (`/auth`) for Gemini Enterprise native OAuth and direct browser login."""
    ge_redirect_uri = request.query_params.get("redirect_uri", "")
    ge_state = request.query_params.get("state", "")
    context_id = request.query_params.get("context_id", "latest")

    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()

    if tenant_id and client_id:
        if ge_redirect_uri:
            # Gemini Enterprise native OAuth popup (registered via authorizationConfig.toolAuthorizations):
            # Pass Gemini Enterprise's registered redirect_uri directly to Microsoft Entra ID so Azure accepts it.
            # Forward the inbound scope/prompt verbatim: `offline_access` is what makes Microsoft
            # Entra return a refresh token, and `prompt=consent` re-issues it after any scope change.
            requested_scope = request.query_params.get("scope", "").strip() or DEFAULT_GRAPH_SCOPE
            if "offline_access" not in requested_scope:
                requested_scope = f"{requested_scope} offline_access"
            params: dict[str, str] = {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": ge_redirect_uri,
                "response_mode": "query",
                "scope": requested_scope,
                "prompt": request.query_params.get("prompt", "consent"),
            }
            if ge_state:
                params["state"] = ge_state
            auth_url = (
                f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/authorize?"
                + urllib.parse.urlencode(params)
            )
            logger.info("Redirecting Gemini Enterprise OAuth popup to Microsoft Entra ID (scope=%s)", requested_scope)
            return RedirectResponse(url=auth_url, status_code=302)

        # Direct browser click without redirect_uri: provide both Web Auth Code flow (using registered vertexaisearch redirect URI) AND Device Code flow
        from qualify.connectors.sharepoint import start_device_code_flow_for_session  # noqa: PLC0415

        dc = start_device_code_flow_for_session(context_id=context_id)
        user_code = dc["user_code"] if dc else "N/A"
        verify_url = dc["verification_uri"] if dc else "https://login.microsoft.com/device"
        safe_ctx = urllib.parse.quote(context_id)

        # Why not `https://vertexaisearch.cloud.google.com/oauth-redirect` here?
        #
        # That endpoint belongs to Gemini Enterprise. It only accepts a `state`
        # that GE itself issued and encrypted. Sending a user there with a state
        # of our own making gets them:
        #
        #   Failed to decrypt the OAuth state parameter:
        #   java.security.GeneralSecurityException: decryption failed
        #
        # The authorization code is still in the address bar behind that error,
        # which is the only reason the copy-paste workaround ever appeared to
        # work. It was never a working flow, just a readable failure.
        #
        # Our own callback has no such problem: we issue the state, so we can
        # decode it. It does have to be registered in Azure first, hence the
        # flag — shipping a button that returns AADSTS50011 is no better.
        base_url = os.environ.get("AGENT_URL", f"https://{request.url.netloc}").rstrip("/")
        callback_uri = f"{base_url}/auth/callback"
        web_oauth_ready = os.environ.get("WEB_OAUTH_CALLBACK") == "1"

        state_payload = base64.urlsafe_b64encode(
            json.dumps({"context_id": context_id}).encode("utf-8")
        ).decode("ascii").rstrip("=")

        web_auth_params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": callback_uri,
            "response_mode": "query",
            "scope": DEFAULT_GRAPH_SCOPE,
            "state": state_payload,
            "prompt": "select_account",
        }
        ms_web_auth_url = (
            f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/authorize?"
            + urllib.parse.urlencode(web_auth_params)
        )

        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Sign in to Microsoft SharePoint</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #0f172a; padding: 2.5rem 1.25rem; max-width: 640px; margin: 0 auto; text-align: center; }}
    .card {{ background: white; border-radius: 12px; padding: 2rem; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border: 1px solid #e2e8f0; margin-bottom: 1.5rem; text-align: left; }}
    .code-box {{ font-family: monospace; font-size: 1.7rem; font-weight: 700; letter-spacing: 0.15rem; background: #f1f5f9; border: 2px dashed #94a3b8; border-radius: 8px; padding: 0.85rem; margin: 1rem 0; color: #0f172a; text-align: center; user-select: all; }}
    .btn {{ display: inline-block; background: #2563eb; color: white; text-decoration: none; padding: 0.75rem 1.4rem; border-radius: 8px; font-weight: 600; font-size: 0.95rem; cursor: pointer; border: none; text-align: center; }}
    .btn:hover {{ background: #1d4ed8; }}
    .btn-success {{ background: #16a34a; }}
    .btn-success:hover {{ background: #15803d; }}
    .input-box {{ width: 100%; box-sizing: border-box; padding: 0.75rem; border: 1px solid #cbd5e1; border-radius: 8px; font-family: monospace; font-size: 0.88rem; margin: 0.6rem 0; }}
    .status-badge {{ display: inline-block; margin-top: 0.8rem; font-size: 0.85rem; color: #64748b; background: #f1f5f9; padding: 0.4rem 0.9rem; border-radius: 999px; }}
    .badge-warn {{ background: #fef3c7; color: #92400e; border: 1px solid #fde68a; border-radius: 8px; padding: 0.75rem; font-size: 0.85rem; margin-top: 0.8rem; display: none; }}
  </style>
</head>
<body>
  <div class="card" id="main-card">
    <h2 style="margin-top:0;text-align:center;">🔐 Connect Microsoft SharePoint Online</h2>
    
    <!-- Method 1: one click, redirecting to our own /auth/callback -->
    {(
        '''<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:1.25rem;margin-bottom:1.5rem;">
      <h3 style="margin-top:0;color:#1e40af;font-size:1.05rem;">⚡ Recommended: one-click sign-in</h3>
      <p style="margin:0.4rem 0 0.9rem;font-size:0.9rem;color:#334155;">
        Sign in with Microsoft and you will be returned here automatically. Nothing to copy.
      </p>
      <p style="margin:0;">
        <a class="btn" href="''' + ms_web_auth_url + '''">Sign in with Microsoft ↗</a>
      </p>
    </div>'''
        if web_oauth_ready
        else '''<div style="background:#fef3c7;border:1px solid #fde68a;border-radius:10px;padding:1.25rem;margin-bottom:1.5rem;">
      <h3 style="margin-top:0;color:#92400e;font-size:1.05rem;">One-click sign-in is not enabled yet</h3>
      <p style="margin:0.4rem 0 0;font-size:0.9rem;color:#334155;">
        It needs this redirect URI registered on the Azure app, then
        <code>WEB_OAUTH_CALLBACK=1</code> on the service:
      </p>
      <p style="margin:0.6rem 0 0;"><code style="font-size:0.82rem;word-break:break-all;">''' + callback_uri + '''</code></p>
      <p style="margin:0.6rem 0 0;font-size:0.9rem;color:#334155;">
        Until then, use the device code below — it works today and needs no copying of URLs.
      </p>
    </div>'''
    )}

    <!-- Method 2: Device Code Flow -->
    <div style="border-top:1px solid #e2e8f0;padding-top:1.2rem;">
      <h3 style="margin-top:0;color:#475569;font-size:0.98rem;">🔑 Alternative: Device Code Flow (Requires Azure "Allow public client flows = Yes")</h3>
      <div class="code-box" id="code">{user_code}</div>
      <div style="text-align:center;">
        <button class="btn" style="background:#475569;" onclick="navigator.clipboard.writeText('{user_code}'); window.open('{verify_url}', '_blank');">
          Copy Code &amp; Open Microsoft Device Login ↗
        </button>
        <div>
          <span class="status-badge" id="poll-status">⏳ Waiting for sign-in completion...</span>
        </div>
        <div class="badge-warn" id="poll-error-box"></div>
      </div>
    </div>
  </div>

  <script>
    const ctxId = "{safe_ctx}";

    function renderSuccess(data) {{
      const card = document.getElementById("main-card");
      if (data.synced && data.synced.syncedUrl) {{
        card.innerHTML = `
          <div style="text-align:center;padding:1rem;">
            <h2 style="color:#16a34a;margin-top:0;">✅ SharePoint Connected &amp; Opportunity Saved!</h2>
            <p style="color:#334155;font-size:1.05rem;">
              The opportunity <strong>${{data.synced.title}}</strong> (<code>${{data.synced.recordId}}</code>) has been saved to the shared SharePoint folder.
            </p>
            <p style="margin: 1.5rem 0;">
              <a class="btn btn-success" href="${{data.synced.syncedUrl}}" target="_blank">📂 Open Opportunity Folder in SharePoint ↗</a>
            </p>
            <p style="color:#16a34a;font-weight:600;font-size:0.95rem;">
              You can now close this window and return to Gemini Enterprise.
            </p>
          </div>
        `;
      }} else if (data.folderUrl) {{
        card.innerHTML = `
          <div style="text-align:center;padding:1rem;">
            <h2 style="color:#16a34a;margin-top:0;">✅ SharePoint Connected &amp; Opportunity Saved!</h2>
            <p style="margin: 1.5rem 0;">
              <a class="btn btn-success" href="${{data.folderUrl}}" target="_blank">📂 Open Opportunity Folder in SharePoint ↗</a>
            </p>
            <p style="color:#16a34a;font-weight:600;font-size:0.95rem;">
              You can now close this window and return to Gemini Enterprise.
            </p>
          </div>
        `;
      }} else {{
        card.innerHTML = `
          <div style="text-align:center;padding:1rem;">
            <h2 style="color:#16a34a;margin-top:0;">✅ Microsoft SharePoint Connected!</h2>
            <p style="color:#334155;font-size:1.05rem;">
              Microsoft SharePoint is now connected to this Gemini Enterprise session.
            </p>
            <p style="color:#475569;font-size:0.95rem;">
              Return to Gemini Enterprise chat and type <strong>save to sharepoint</strong> (or submit Stage 4) to save the opportunity to the shared SharePoint folder.
            </p>
          </div>
        `;
      }}
    }}

    // The paste-a-URL flow is gone: it only ever existed to recover an
    // authorization code from Gemini Enterprise's error page. `/auth/exchange`
    // is still served, because a code pasted into chat still goes through it.

    const timer = setInterval(async () => {{
      try {{
        const resp = await fetch(`/auth/status?context_id=${{ctxId}}`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.authenticated) {{
          clearInterval(timer);
          renderSuccess(data);
        }} else if (data.error) {{
          const errBox = document.getElementById("poll-error-box");
          if (errBox) {{
            errBox.style.display = "block";
            errBox.innerHTML = `<strong>⚠️ Note on Device Code:</strong> Azure blocked Device Code token redemption because "Allow public client flows" is disabled in Azure Portal. <strong>Please use Method 1 (top blue box) above!</strong>`;
          }}
        }}
      }} catch (e) {{}}
    }}, 2000);
  </script>
</body>
</html>"""
        return HTMLResponse(html)

    if not ge_redirect_uri:
        return JSONResponse({"error": "Missing redirect_uri and MS_GRAPH_CLIENT_ID not configured"}, status_code=400)

    code = secrets.token_urlsafe(24)
    sep = "&" if "?" in ge_redirect_uri else "?"
    target_url = f"{ge_redirect_uri}{sep}code={code}&state={ge_state}"
    return RedirectResponse(url=target_url, status_code=302)


async def handle_oauth_exchange(request: Request) -> Response:
    """POST `/auth/exchange` endpoint to exchange a pasted Microsoft OAuth redirect URL or code."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    code_or_url = str(body.get("code_or_url") or "")
    redirect_uri = str(body.get("redirect_uri") or "https://vertexaisearch.cloud.google.com/oauth-redirect")
    context_id = str(body.get("context_id") or "latest")
    from qualify.connectors.sharepoint import exchange_auth_code_for_session  # noqa: PLC0415

    res = exchange_auth_code_for_session(
        code_or_url=code_or_url,
        redirect_uri=redirect_uri,
        context_id=context_id,
    )
    return JSONResponse(res)


async def handle_oauth_status(request: Request) -> Response:
    """Returns JSON status of whether the user session is authenticated with Microsoft SharePoint and any auto-synced folder URL."""
    context_id = request.query_params.get("context_id") or "latest"
    from qualify.connectors.sharepoint import (
        get_cached_delegated_token,
        get_poll_error,
        get_synced_result,
        load_delegated_refresh_token,
    )

    has_token = bool(
        get_cached_delegated_token(context_id)
        or get_cached_delegated_token("latest")
        or load_delegated_refresh_token(context_id)
    )
    synced = get_synced_result(context_id)
    poll_err = get_poll_error(context_id)
    return JSONResponse(
        {
            "authenticated": has_token,
            "contextId": context_id,
            "synced": synced,
            "error": poll_err,
        }
    )



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

    # Decoded before the exchange because the token has to be vaulted against
    # the conversation that started the sign-in. Falling back to "latest" would
    # work for a single user and silently cross wires for two.
    state_data: dict[str, Any] = {}
    if state_str:
        try:
            padded = state_str + "=" * (-len(state_str) % 4)
            state_data = json.loads(
                base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            )
        except Exception:
            logger.debug("OAuth state was not our own base64 JSON envelope; ignoring")
    context_id = str(state_data.get("context_id") or "latest")

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
                save_delegated_refresh_token(
                    refresh_token, access_token, expires_in, context_id=context_id
                )
                logger.info(
                    "Exchanged Microsoft Entra authorization code for context %s", context_id
                )
                # Write anything queued while the user was unauthenticated.
                # GE never polls the task after an out-of-band sign-in, so if
                # we skip this the record sits waiting until the user thinks to
                # ask a second time.
                try:
                    synced = auto_sync_pending_records(access_token, context_id=context_id)
                    if synced:
                        logger.info("Auto-synced pending record for context %s", context_id)
                except Exception as exc:
                    logger.warning("Post-login auto-sync failed for %s: %s", context_id, exc)
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

    If Gemini Enterprise sends a Microsoft `authorization_code` (received from `/auth` redirect),
    exchanges it with Microsoft Entra ID for the user's personal Delegated Access & Refresh Token
    and caches it in the server vault.
    Returns a Cloud Run-compatible Google OIDC transport token as `access_token` so Gemini Enterprise's
    subsequent `Authorization: Bearer <token>` calls to `POST /mcp` pass Google Cloud Run Frontend (GFE)
    with 200 OK, while our server executes all SharePoint Graph API queries on behalf of the user.
    """
    form_data: dict[str, str] = {}
    try:
        raw_form = await request.form()
        form_data = {k: str(v) for k, v in raw_form.items()}
    except Exception:
        pass

    code = form_data.get("code") or request.query_params.get("code", "")
    redirect_uri = form_data.get("redirect_uri") or request.query_params.get("redirect_uri", "")
    grant_type = form_data.get("grant_type") or request.query_params.get("grant_type", "")

    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "").strip()
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MS_GRAPH_CLIENT_SECRET", "").strip()

    if code and tenant_id and client_id and not code.startswith("refresh_"):
        token_url = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token"
        try:
            with httpx.Client(timeout=10.0) as client:
                payload = {
                    "client_id": client_id,
                    "grant_type": grant_type or "authorization_code",
                    "code": code,
                    "scope": "https://graph.microsoft.com/Sites.ReadWrite.All offline_access",
                }
                if redirect_uri:
                    payload["redirect_uri"] = redirect_uri
                if client_secret:
                    payload["client_secret"] = client_secret
                resp = client.post(
                    token_url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    access_token = data.get("access_token", "")
                    refresh_token = data.get("refresh_token", "")
                    expires_in = int(data.get("expires_in", 3599))
                    save_delegated_refresh_token(refresh_token, access_token, expires_in)
                    logger.info("Successfully captured Microsoft Delegated User Token & Refresh Token via /token exchange.")
                else:
                    logger.warning("Microsoft token exchange in /token returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Microsoft token exchange in /token failed: %s", exc)

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
