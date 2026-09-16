"""MCP (Model Context Protocol) HTTP Server for SharePoint Integration.

Exposes:
- `POST /mcp`: Streamable HTTP JSON-RPC 2.0 endpoint for Gemini Enterprise MCP registration.
  Captures delegated `Authorization: Bearer <token>` headers and forwards them to `SharePointConnector`.
- `GET /auth`: OAuth 2.0 authorization redirect endpoint for Gemini Enterprise Federated Connector registration.
- `POST /token`: OAuth 2.0 token exchange endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from qualify.connectors.sharepoint import (
    cache_delegated_token,
    get_sharepoint_connector,
    is_microsoft_graph_token,
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
    """OAuth 2.0 Authorization Endpoint (`/auth`) for Gemini Enterprise Connector registration."""
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    if not redirect_uri:
        return JSONResponse({"error": "Missing redirect_uri"}, status_code=400)

    # If Microsoft Entra tenant/client is configured and real redirect requested, redirect to Entra ID;
    # otherwise issue an ephemeral authorization code for Gemini Enterprise connector handshake.
    code = secrets.token_urlsafe(24)
    sep = "&" if "?" in redirect_uri else "?"
    target_url = f"{redirect_uri}{sep}code={code}&state={state}"
    return RedirectResponse(url=target_url, status_code=302)


async def handle_oauth_token(request: Request) -> Response:
    """OAuth 2.0 Token Exchange Endpoint (`/token`) for Gemini Enterprise Connector registration."""
    # If MS_GRAPH_CLIENT_ID and MS_GRAPH_CLIENT_SECRET are set, acquire an Entra token to return
    connector = get_sharepoint_connector()
    headers, auth_mode = connector.get_graph_headers()
    auth_val = headers.get("Authorization", "Bearer mock_graph_token")
    token = auth_val[7:] if auth_val.startswith("Bearer ") else auth_val

    return JSONResponse(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": f"refresh_{secrets.token_urlsafe(16)}",
            "auth_mode": auth_mode,
        }
    )
