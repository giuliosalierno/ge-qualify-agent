"""MCP (Model Context Protocol) endpoints for SharePoint Federated Connector integration."""

from qualify.mcp.sharepoint_mcp import (
    handle_mcp_request,
    handle_oauth_auth,
    handle_oauth_callback,
    handle_oauth_status,
)

__all__ = [
    "handle_mcp_request",
    "handle_oauth_auth",
    "handle_oauth_callback",
    "handle_oauth_status",
]
