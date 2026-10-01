"""Redirect handler for GET / behind Google Cloud IAP.

When a user opens the Load Balancer URL, Google Cloud IAP authenticates their
Google Identity first, and GET / immediately 302-redirects them to the native
Gemini Enterprise Web App configured in ``GE_WEB_APP_URL``. Deployments without
that variable (e.g. go/demos Click-to-Deploy, which has no Load Balancer)
answer 404 rather than redirecting to another deployment's app.
"""

from __future__ import annotations

import hashlib
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse


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


async def handle_web_ui(request: Request) -> RedirectResponse | PlainTextResponse:
    """Redirects authenticated IAP visitors directly to the native Gemini Enterprise Web App."""
    target_url = os.environ.get("GE_WEB_APP_URL", "").strip()
    if not target_url.startswith("https://"):
        return PlainTextResponse("Not found", status_code=404)
    return RedirectResponse(url=target_url, status_code=302)
