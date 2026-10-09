"""Storage connectors for qualification records (SharePoint, Google Drive).

Agent code should use the provider-neutral API in `storage.py`; the provider
modules are selected by `STORAGE_PROVIDER`.
"""

from qualify.connectors.sharepoint import (
    SharePointConnector,
    SharePointSyncResult,
    cache_delegated_token,
    get_sharepoint_connector,
    sync_to_optional_sharepoint,
)
from qualify.connectors.storage import (
    StorageAuthRequired,
    StorageConnector,
    StorageSyncResult,
    get_storage_connector,
    storage_provider,
    sync_to_storage,
)

__all__ = [
    "SharePointConnector",
    "SharePointSyncResult",
    "StorageAuthRequired",
    "StorageConnector",
    "StorageSyncResult",
    "cache_delegated_token",
    "get_sharepoint_connector",
    "get_storage_connector",
    "storage_provider",
    "sync_to_optional_sharepoint",
    "sync_to_storage",
]
