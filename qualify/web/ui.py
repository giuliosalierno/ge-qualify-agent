"""Redirect handler for GET / behind Google Cloud IAP.

When a user accesses https://8.233.121.252.nip.io/, Google Cloud IAP authenticates
their Google Identity first, and GET / immediately 302-redirects them to the native
Gemini Enterprise Web App.
"""

from __future__ import annotations

import hashlib
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

DEFAULT_GE_WEB_APP_URL = (
    "https://vertexaisearch.cloud.google.com/home/cid/"
    "53514d2f-4bc9-479d-9489-9503a5bf8332?hl=en_US"
)


def _extract_iap_email(request: Request) -> str:
    """Extracts the authenticated Google Identity email from Cloud IAP headers."""
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    if ":" in raw:
        return raw.split(":", 1)[1].strip()
    return raw.strip() or "authenticated-user@google.com"


async def handle_whoami(request: Request) -> JSONResponse:
    """Returns the signed-in Google Identity (via Cloud IAP)."""
    email = _extract_iap_email(request)
    user_hash = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:12]
    return JSONResponse(
        {
            "email": email,
            "user_hash": user_hash,
            "default_context_id": f"iap-{user_hash}",
        }
    )


async def handle_web_ui(request: Request) -> RedirectResponse:
    """Redirects authenticated IAP visitors directly to the native Gemini Enterprise Web App."""
    target_url = os.environ.get("GE_WEB_APP_URL", DEFAULT_GE_WEB_APP_URL)
    return RedirectResponse(url=target_url, status_code=302)
