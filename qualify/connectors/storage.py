"""Provider-neutral storage interface for qualification records.

One storage provider is active per deployment, chosen by ``STORAGE_PROVIDER``:

- ``sharepoint`` (default): Microsoft SharePoint via Microsoft Graph.
- ``gdrive``: Google Drive, in the signed-in user's My Drive (``drive.file``).
- ``none``: no document storage. Deliverables are rendered in chat only and
  the durable record store (``QUALIFY_GCS_BUCKET``) is the single source of
  truth for portfolio and handover. Used by the go/demos Click-to-Deploy
  build, where testers have no Microsoft tenant or Drive consent.

Callers in the agent (`turn.py`, `handover.py`) talk to
:func:`get_storage_connector` and never import a provider module directly, so
switching the demo from SharePoint to Drive is a configuration change rather
than a code change.

Every provider follows the same auth contract: a live call without a signed-in
user for *this* conversation yields ``auth_mode == "unauthenticated"`` (or
raises :class:`StorageAuthRequired` for reads), and the caller shows the
sign-in card. There is never a silent fallback to another identity.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from qualify.connectors import token_vault

if TYPE_CHECKING:
    from qualify.schema.use_case_record import UseCaseRecord

logger = logging.getLogger(__name__)

#: Providers this build knows how to construct.
SUPPORTED_PROVIDERS = ("sharepoint", "gdrive", "none")
DEFAULT_PROVIDER = "sharepoint"
DISABLED_PROVIDER = "none"


@dataclass
class StorageSyncResult:
    """Result of writing a qualification opportunity to the storage provider.

    ``auth_mode`` is one of ``delegated`` (this conversation's signed-in user),
    ``client_credentials`` (SharePoint app-only, opt-in), ``mock`` (local
    development) or ``unauthenticated`` (sign-in needed; nothing was written).
    """

    success: bool
    record_id: str
    folder_url: str
    brief_url: str
    list_item_id: str | None = None
    auth_mode: str = "mock"
    message: str = ""


class StorageAuthRequired(RuntimeError):
    """A live storage call needs a signed-in user and this conversation has none."""


class StorageDisabled(RuntimeError):
    """This deployment runs with ``STORAGE_PROVIDER=none``."""


@runtime_checkable
class StorageConnector(Protocol):
    """What the agent needs from a storage provider.

    Attributes:
        provider_id: Stable id, matching ``STORAGE_PROVIDER``.
        display_name: Product name shown to users, e.g. "Google Drive".
        account_label: Identity provider shown on buttons, e.g. "Google".
    """

    provider_id: str
    display_name: str
    account_label: str

    def sync_opportunity(
        self,
        record: "UseCaseRecord",
        *,
        skipped_stages: set[int] | None = None,
        delegated_token: str | None = None,
        context_id: str | None = None,
        pack_name: str = "business",
    ) -> StorageSyncResult: ...

    def list_opportunities(
        self,
        query: str = "",
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        pending_technical_review: bool = False,
        limit: int = 25,
    ) -> list[dict[str, Any]]: ...

    def load_opportunity(
        self,
        query_or_record_id: str,
        delegated_token: str | None = None,
        context_id: str | None = None,
    ) -> "UseCaseRecord | None": ...

    def load_all_opportunities(
        self,
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        limit: int = 50,
    ) -> list[tuple["UseCaseRecord", dict[str, Any]]]: ...

    def sync_portfolio_report(
        self,
        report_md: str,
        delegated_token: str | None = None,
        *,
        context_id: str | None = None,
        filename: str = "Portfolio_Prioritization_Report.md",
    ) -> str | None: ...


def storage_provider() -> str:
    """The active provider id. Unknown values fail loudly rather than silently."""
    value = os.environ.get("STORAGE_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if value not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported STORAGE_PROVIDER={value!r}; expected one of {SUPPORTED_PROVIDERS}"
        )
    return value


def storage_enabled() -> bool:
    """False when the deployment has no document storage (``none``)."""
    return storage_provider() != DISABLED_PROVIDER


def get_storage_connector() -> StorageConnector:
    """Returns the connector for the active provider.

    Resolves each provider's own singleton accessor through its module at call
    time, so tests that patch e.g. ``sharepoint.get_sharepoint_connector``
    keep working.

    Raises:
        StorageDisabled: ``STORAGE_PROVIDER=none``. Callers already treat a
            raising connector as "use the record store", which is exactly the
            behaviour wanted when there is no document storage.
    """
    provider = storage_provider()
    if provider == DISABLED_PROVIDER:
        raise StorageDisabled("Document storage is disabled (STORAGE_PROVIDER=none)")
    if provider == "gdrive":
        from qualify.connectors import gdrive  # noqa: PLC0415

        return gdrive.get_gdrive_connector()

    from qualify.connectors import sharepoint  # noqa: PLC0415

    return sharepoint.get_sharepoint_connector()


def is_connected(context_id: str | None) -> bool:
    """True if this conversation currently holds a live access token."""
    if not storage_enabled():
        return False
    return bool(token_vault.get_access(context_id))


def sync_record(
    connector: StorageConnector,
    record: "UseCaseRecord",
    *,
    skipped_stages: set[int] | None = None,
    delegated_token: str | None = None,
    context_id: str | None = None,
    pack_name: str = "business",
) -> StorageSyncResult | None:
    """Writes a record through `connector`, queueing it if sign-in is needed.

    Never raises: a storage outage must not break the chat turn. A record that
    could not be written as the conversation's own user is queued for that
    conversation only, and written by :func:`auto_sync_pending` once the user
    signs in. The pack name is queued with it so a pending technical dossier is
    not later written as a business brief.
    """
    try:
        res = connector.sync_opportunity(
            record,
            skipped_stages=skipped_stages,
            delegated_token=delegated_token,
            context_id=context_id,
            pack_name=pack_name,
        )
        if res and context_id:
            if res.auth_mode != "delegated":
                token_vault.queue_pending(context_id, record, skipped_stages, pack_name)
            else:
                token_vault.drop_pending(context_id)
        return res
    except Exception as exc:
        logger.warning("Storage sync skipped due to error: %s", exc)
        return None


def sync_to_storage(
    record: "UseCaseRecord",
    *,
    skipped_stages: set[int] | None = None,
    context_id: str | None = None,
    pack_name: str = "business",
) -> StorageSyncResult | None:
    """Writes a record to the active provider. See :func:`sync_record`."""
    try:
        if not storage_enabled():
            return None
        connector = get_storage_connector()
    except Exception as exc:
        logger.warning("Storage connector unavailable: %s", exc)
        return None
    return sync_record(
        connector,
        record,
        skipped_stages=skipped_stages,
        context_id=context_id,
        pack_name=pack_name,
    )


def auto_sync_pending(
    access_token: str,
    context_id: str,
    connector: StorageConnector | None = None,
) -> StorageSyncResult | None:
    """Writes this conversation's queued record right after its user signs in.

    Gemini Enterprise never polls the task after an out-of-band sign-in, so
    without this the record would wait until the user asks a second time.
    """
    pending = token_vault.pop_pending(context_id)
    if not pending:
        return None
    record, skipped, pack_name = pending
    logger.info(
        "Auto-syncing pending opportunity %s after user sign-in", record.meta.record_id
    )
    try:
        target = connector or get_storage_connector()
    except Exception as exc:
        logger.warning("Auto-sync after sign-in failed: %s", exc)
        return None
    return sync_record(
        target,
        record,
        skipped_stages=skipped,
        delegated_token=access_token,
        context_id=context_id,
        pack_name=pack_name,
    )
