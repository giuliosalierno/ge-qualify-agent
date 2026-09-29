"""Verifies that A2A calls really come from Gemini Enterprise.

Why: the Cloud Run service is deployed ``--allow-unauthenticated`` because
the browser must reach ``/auth`` and ``/auth/callback`` directly during
Microsoft sign-in. That also leaves the A2A endpoint (``POST /``) open to
anyone who knows the ``run.app`` URL, who could then drive the agent and
spend its Gemini quota.

Gemini Enterprise attaches a Google-signed OIDC ID token for its Discovery
Engine service agent to every call, in ``X-Serverless-Authorization``
(observed 2026-09-29; there is no ``Authorization`` header). This middleware
verifies that token for the A2A routes only:

- signature against Google's public certs (cached per ``Cache-Control``),
- issuer ``accounts.google.com``,
- audience in ``A2A_AUDIENCES`` (default: ``AGENT_URL``),
- ``email`` in ``A2A_ALLOWED_INVOKERS`` and ``email_verified``.

Modes (``A2A_AUTH_MODE``):

- ``enforce``: reject unverified calls with 401. Default on Cloud Run
  (``K_SERVICE`` set), so a missing setting fails closed.
- ``log``: verify and log the outcome, never reject. For rollout, to confirm
  the audience GE actually uses before enforcing.
- ``off``: skip entirely. Default for local development and tests.

Token values are never logged. Only the outcome, the audience and a service
account email are, and a human email is reduced to its domain.

This is a pure ASGI middleware: it reads headers and then hands the untouched
``scope``/``receive``/``send`` to the app. It must never wrap the response
stream (see the note on ``message/stream`` in ``server.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from google.auth import exceptions as google_auth_exceptions
from google.auth import jwt as google_jwt

log = logging.getLogger(__name__)

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

#: Routes that execute agent logic. The public agent card stays open: GE
#: fetches it during registration and it holds nothing secret.
PROTECTED_POST_PATHS = frozenset({"/"})
PROTECTED_GET_PATHS = frozenset({"/agent/authenticatedExtendedCard"})

Scope = dict[str, Any]
ASGIApp = Callable[[Scope, Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    audience: str = ""
    principal: str = ""


def auth_mode() -> str:
    """`enforce`, `log` or `off`. Unknown values are treated as `enforce`."""
    default = "enforce" if os.environ.get("K_SERVICE") else "off"
    mode = os.environ.get("A2A_AUTH_MODE", default).strip().lower()
    return mode if mode in ("enforce", "log", "off") else "enforce"


def _csv_env(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def allowed_audiences() -> list[str]:
    configured = _csv_env("A2A_AUDIENCES")
    if configured:
        return configured
    agent_url = os.environ.get("AGENT_URL", "").strip().rstrip("/")
    return [agent_url, f"{agent_url}/"] if agent_url else []


def allowed_invokers() -> set[str]:
    configured = {e.lower() for e in _csv_env("A2A_ALLOWED_INVOKERS")}
    if configured:
        return configured
    project_number = os.environ.get("PROJECT_NUMBER", "").strip()
    if project_number:
        return {f"service-{project_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"}
    return set()


class _CertCache:
    """Google's OIDC signing certs, refreshed per the response's max-age."""

    def __init__(self) -> None:
        self._certs: dict[str, str] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, str]:
        if self._certs and time.time() < self._expires_at:
            return self._certs
        async with self._lock:
            if self._certs and time.time() < self._expires_at:
                return self._certs
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(GOOGLE_CERTS_URL)
            resp.raise_for_status()
            max_age = 3600
            match = re.search(r"max-age=(\d+)", resp.headers.get("cache-control", ""))
            if match:
                max_age = int(match.group(1))
            self._certs = resp.json()
            self._expires_at = time.time() + min(max_age, 6 * 3600)
            return self._certs


_CERTS = _CertCache()


def _redact_principal(email: str) -> str:
    """Service accounts are logged in full; people only by domain."""
    if email.endswith(".gserviceaccount.com"):
        return email
    return f"<user@{email.split('@', 1)[1]}>" if "@" in email else "<unknown>"


def _extract_token(headers: dict[str, str]) -> str:
    for name in ("x-serverless-authorization", "authorization"):
        value = headers.get(name, "").strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
    return ""


async def verify_request_headers(
    headers: dict[str, str],
    *,
    certs: dict[str, str] | None = None,
) -> Verdict:
    """Checks the caller's ID token. `certs` is injectable for tests."""
    token = _extract_token(headers)
    if not token:
        return Verdict(False, "no_token")
    if token.count(".") != 2:
        # Cloud Run strips the signature from tokens it verified itself; an
        # unsigned token is no proof of anything here.
        return Verdict(False, "not_a_signed_jwt")

    audiences = allowed_audiences()
    invokers = allowed_invokers()
    if not audiences or not invokers:
        return Verdict(False, "not_configured")

    try:
        cert_map = certs if certs is not None else await _CERTS.get()
    except Exception as exc:
        return Verdict(False, f"certs_unavailable:{type(exc).__name__}")

    try:
        claims = google_jwt.decode(token, certs=cert_map, audience=audiences, clock_skew_in_seconds=30)
    except (google_auth_exceptions.GoogleAuthError, ValueError) as exc:
        # Report the audience even on failure, so `log` mode can tell us what
        # GE sends. Unverified claims are used for that diagnostic only.
        aud = ""
        try:
            aud = str(google_jwt.decode(token, verify=False).get("aud", ""))
        except Exception:
            pass
        reason = "bad_audience" if "audience" in str(exc).lower() else "invalid_token"
        return Verdict(False, reason, audience=aud)

    email = str(claims.get("email", "")).lower()
    aud = str(claims.get("aud", ""))
    if claims.get("iss") not in GOOGLE_ISSUERS:
        return Verdict(False, "bad_issuer", aud, _redact_principal(email))
    if not claims.get("email_verified") or email not in invokers:
        return Verdict(False, "caller_not_allowed", aud, _redact_principal(email))
    return Verdict(True, "ok", aud, _redact_principal(email))


def _is_protected(scope: Scope) -> bool:
    path = scope.get("path", "")
    method = scope.get("method", "")
    return (method == "POST" and path in PROTECTED_POST_PATHS) or (
        method == "GET" and path in PROTECTED_GET_PATHS
    )


class GeminiEnterpriseAuthMiddleware:
    """Rejects (or logs) A2A calls that don't carry a valid GE identity token."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope.get("type") != "http" or not _is_protected(scope):
            await self.app(scope, receive, send)
            return

        mode = auth_mode()
        if mode == "off":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        verdict = await verify_request_headers(headers)
        log.info(
            "A2A_AUTH mode=%s ok=%s reason=%s aud=%s principal=%s",
            mode, verdict.ok, verdict.reason, verdict.audience or "-", verdict.principal or "-",
        )

        if verdict.ok or mode == "log":
            await self.app(scope, receive, send)
            return

        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
