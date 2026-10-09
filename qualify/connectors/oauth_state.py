"""Signed, expiring tokens that bind a SharePoint sign-in to one conversation.

Why this exists: a Microsoft token is vaulted against the conversation
(`context_id`) that asked for it. If that binding came from an unsigned query
parameter, anyone could craft `/auth?context_id=<someone else's>` and attach
their own Microsoft account to another user's conversation — every later save
from that conversation would then land in the attacker's SharePoint.

Two token purposes are issued, and one can never be replayed as the other:

- ``link``:  put in the sign-in URL the agent shows in chat. Long-lived
  (the user may click it a while after it was rendered).
- ``state``: the OAuth ``state`` sent to Microsoft. Short-lived, single sign-in.

Format: ``base64url(json payload) "." base64url(HMAC-SHA256(payload))``.

The key comes from ``OAUTH_STATE_SECRET`` (Secret Manager in production). If it
is unset a random per-process key is generated: fine for a single-instance
demo, but every restart invalidates outstanding sign-in links.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse

logger = logging.getLogger(__name__)

LINK_TTL_SECONDS = 24 * 3600
STATE_TTL_SECONDS = 10 * 60

_EPHEMERAL_KEY: bytes | None = None
# Turns run on worker threads; without this two first turns could each mint a
# key, and links signed with the losing one would never verify.
_EPHEMERAL_KEY_LOCK = threading.Lock()


def _key() -> bytes:
    global _EPHEMERAL_KEY
    configured = os.environ.get("OAUTH_STATE_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    with _EPHEMERAL_KEY_LOCK:
        if _EPHEMERAL_KEY is None:
            logger.warning(
                "OAUTH_STATE_SECRET is not set; using a random per-process key. "
                "Sign-in links will stop working after a restart."
            )
            _EPHEMERAL_KEY = secrets.token_bytes(32)
        return _EPHEMERAL_KEY


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest())


def issue(context_id: str, purpose: str, ttl_seconds: int) -> str:
    """Returns a signed token binding `context_id` to `purpose` until expiry."""
    if not context_id:
        raise ValueError("context_id is required")
    payload = {
        "c": context_id,
        "p": purpose,
        "e": int(time.time()) + ttl_seconds,
        "n": secrets.token_urlsafe(8),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def verify(token: str, purpose: str) -> str | None:
    """Returns the bound `context_id` if `token` is authentic, unexpired and for `purpose`."""
    if not token or token.count(".") != 1:
        return None
    body, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("p") != purpose:
        return None
    if int(payload.get("e", 0)) < time.time():
        return None
    context_id = payload.get("c")
    return context_id if isinstance(context_id, str) and context_id else None


def build_signin_url(base_url: str, context_id: str) -> str:
    """The `/auth` link shown to a user, carrying a signed conversation binding."""
    token = issue(context_id, "link", LINK_TTL_SECONDS)
    return f"{base_url.rstrip('/')}/auth?t={urllib.parse.quote(token)}"
