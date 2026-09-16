"""Connectors for external enterprise platforms (SharePoint / Microsoft Graph)."""

from qualify.connectors.sharepoint import (
    SharePointConnector,
    SharePointSyncResult,
    cache_delegated_token,
    get_sharepoint_connector,
    sync_to_optional_sharepoint,
)

__all__ = [
    "SharePointConnector",
    "SharePointSyncResult",
    "cache_delegated_token",
    "get_sharepoint_connector",
    "sync_to_optional_sharepoint",
]
