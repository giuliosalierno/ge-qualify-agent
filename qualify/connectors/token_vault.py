"""Per-conversation credential vault shared by every storage provider.

Extracted from `sharepoint.py` so that Google Drive and Microsoft SharePoint
share one audited implementation of the security model rather than two copies
that can drift apart.

Security model (unchanged from the SharePoint-only design):

- Tokens are keyed by the conversation (`context_id`) that completed the
  sign-in. That binding is proven by a signed OAuth `state`
  (see `oauth_state.py`), never taken from a query string.
- There is no shared or fallback key. A conversation only ever uses its own
  token; without one, callers get `unauthenticated` and show the sign-in card.
- Nothing is written to disk, GCS, logs or `os.environ`. A restart signs
  everyone out, which is the intended trade-off.
- Token values are never logged. Only the conversation id is.

Exactly one storage provider is active per deployment (`STORAGE_PROVIDER`), so
the vault is keyed by conversation alone. Validating that a token belongs to
the active provider is the provider module's job, before it calls `put_access`.

The three dicts are module-level on purpose: `sharepoint.py` re-exports them
under their historical names (`_TOKEN_VAULT`, `_REFRESH_VAULT`,
`_PENDING_RECORDS`), and because they are the *same objects* existing tests
that clear or inspect those names keep working.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

    from qualify.schema.use_case_record import UseCaseRecord

logger = logging.getLogger(__name__)

#: context_id -> (access_token, expires_at_unix_ts)
ACCESS: dict[str, tuple[str, float]] = {}
#: context_id -> refresh_token
REFRESH: dict[str, str] = {}
#: context_id -> (record, skipped_stages, pack_name), awaiting sign-in.
PENDING: dict[str, tuple["UseCaseRecord", set[int], str]] = {}


def put_access(context_id: str, token: str, ttl_seconds: int = 3600) -> None:
    """Stores an access token for one conversation until `ttl_seconds` from now."""
    if not context_id or not token:
        return
    ACCESS[context_id] = (token, time.time() + ttl_seconds)
    logger.info("Cached delegated user token (context=%s, ttl=%ds)", context_id, ttl_seconds)


def get_access(context_id: str | None) -> str | None:
    """Returns this conversation's non-expired access token, or None."""
    if not context_id:
        return None
    entry = ACCESS.get(context_id)
    if entry is None:
        return None
    token, expires_at = entry
    if time.time() < expires_at:
        return token
    ACCESS.pop(context_id, None)
    return None


def save_tokens(
    context_id: str,
    *,
    refresh_token: str = "",
    access_token: str = "",
    expires_in: int = 3599,
) -> None:
    """Vaults a user's tokens for exactly one conversation.

    The access token is kept for one minute less than the provider says, so a
    request never goes out with a token that expires in flight.
    """
    if not context_id:
        raise ValueError("context_id is required to store user tokens")
    if refresh_token:
        REFRESH[context_id] = refresh_token
    if access_token:
        put_access(context_id, access_token, ttl_seconds=max(60, expires_in - 60))


def get_refresh(context_id: str | None) -> str:
    """Returns this conversation's refresh token, or an empty string."""
    if not context_id:
        return ""
    return REFRESH.get(context_id, "")


def has_session(context_id: str | None) -> bool:
    """True if this conversation holds a usable access token or a refresh token."""
    return bool(get_access(context_id) or get_refresh(context_id))


def clear(context_id: str) -> None:
    """Forgets every token held for a conversation."""
    ACCESS.pop(context_id, None)
    REFRESH.pop(context_id, None)


def forget_rejected_token(response: "httpx.Response") -> None:
    """httpx response hook: a 401 on a vaulted user token signs that conversation out.

    The vault keeps an access token until its stated expiry, but Microsoft or
    Google can revoke it sooner (password change, admin revocation, consent
    withdrawn). Without this the agent would keep sending a dead token and
    report the provider as "unavailable". Clearing both tokens makes the next
    check see "not signed in", which is what triggers the sign-in prompt. The
    refresh token goes too: whatever revoked the access token has almost
    always revoked it as well.
    """
    if response.status_code != 401:
        return
    auth = response.request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return
    token = auth[7:].strip()
    for context_id, (held, _exp) in list(ACCESS.items()):
        if held == token:
            clear(context_id)
            logger.warning("Provider rejected the user token (401); signed out context=%s", context_id)


#: Pass as ``httpx.Client(event_hooks=HTTP_HOOKS)`` on every provider call.
HTTP_HOOKS: dict[str, list] = {"response": [forget_rejected_token]}


def queue_pending(
    context_id: str,
    record: "UseCaseRecord",
    skipped_stages: set[int] | None,
    pack_name: str,
) -> None:
    """Holds a record for this conversation until its user signs in."""
    if context_id:
        PENDING[context_id] = (record, set(skipped_stages or set()), pack_name)


def pop_pending(
    context_id: str | None,
) -> tuple["UseCaseRecord", set[int], str] | None:
    """Removes and returns this conversation's queued record, if any."""
    if not context_id:
        return None
    return PENDING.pop(context_id, None)


def drop_pending(context_id: str | None) -> None:
    """Forgets a queued record, e.g. after it was written directly."""
    if context_id:
        PENDING.pop(context_id, None)
