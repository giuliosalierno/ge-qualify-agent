"""GE caller verification: real RS256 tokens signed with a throwaway key."""

from __future__ import annotations

import time
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt
from google.auth import jwt as google_jwt
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import qualify.agent.ge_auth as ge_auth

AUD = "https://agent.example.run.app"
GE_SA = "service-123@gcp-sa-discoveryengine.iam.gserviceaccount.com"


def _keypair(kid: str) -> tuple[crypt.RSASigner, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return crypt.RSASigner.from_string(priv, key_id=kid), pub


SIGNER, PUB = _keypair("k1")
OTHER_SIGNER, _ = _keypair("k1")  # same kid, different key: a forgery
CERTS = {"k1": PUB}


def _token(signer: crypt.RSASigner = SIGNER, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": AUD,
        "email": GE_SA,
        "email_verified": True,
        "iat": now,
        "exp": now + 600,
        "sub": "1234",
    }
    claims.update(overrides)
    return google_jwt.encode(signer, claims).decode()


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_AUDIENCES", AUD)
    monkeypatch.setenv("A2A_ALLOWED_INVOKERS", GE_SA)
    monkeypatch.delenv("K_SERVICE", raising=False)

    async def _fake_certs(self: Any) -> dict[str, str]:
        return CERTS

    monkeypatch.setattr(ge_auth._CertCache, "get", _fake_certs)


async def _verify(token: str | None, header: str = "x-serverless-authorization") -> ge_auth.Verdict:
    headers = {header: f"Bearer {token}"} if token is not None else {}
    return await ge_auth.verify_request_headers(headers, certs=CERTS)


# --- verification ----------------------------------------------------------


@pytest.mark.anyio
async def test_valid_ge_token_passes() -> None:
    v = await _verify(_token())
    assert v.ok and v.principal == GE_SA and v.audience == AUD


@pytest.mark.anyio
async def test_authorization_header_is_accepted_too() -> None:
    assert (await _verify(_token(), header="authorization")).ok


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("token_kwargs", "reason"),
    [
        ({"aud": "https://evil.example"}, "bad_audience"),
        ({"email": "someone@gcp-sa-other.iam.gserviceaccount.com"}, "caller_not_allowed"),
        ({"email_verified": False}, "caller_not_allowed"),
        ({"iss": "https://evil.example"}, "bad_issuer"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "invalid_token"),
    ],
)
async def test_bad_claims_are_rejected(token_kwargs: dict[str, Any], reason: str) -> None:
    v = await _verify(_token(**token_kwargs))
    assert not v.ok and v.reason == reason


@pytest.mark.anyio
async def test_forged_signature_is_rejected() -> None:
    v = await _verify(_token(signer=OTHER_SIGNER))
    assert not v.ok and v.reason == "invalid_token"


@pytest.mark.anyio
async def test_missing_and_unsigned_tokens_are_rejected() -> None:
    assert (await _verify(None)).reason == "no_token"
    header, payload, _sig = _token().split(".")
    assert (await _verify(f"{header}.{payload}")).reason == "not_a_signed_jwt"


@pytest.mark.anyio
async def test_unconfigured_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_AUDIENCES")
    monkeypatch.delenv("AGENT_URL", raising=False)
    assert (await _verify(_token())).reason == "not_configured"


def test_human_principals_are_reduced_to_domain() -> None:
    assert ge_auth._redact_principal("alice@altostrat.com") == "<user@altostrat.com>"
    assert ge_auth._redact_principal(GE_SA) == GE_SA


def test_default_invoker_from_project_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_ALLOWED_INVOKERS")
    monkeypatch.setenv("PROJECT_NUMBER", "369594916120")
    assert ge_auth.allowed_invokers() == {
        "service-369594916120@gcp-sa-discoveryengine.iam.gserviceaccount.com"
    }


# --- mode selection --------------------------------------------------------


def test_mode_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_AUTH_MODE", raising=False)
    assert ge_auth.auth_mode() == "off"
    monkeypatch.setenv("K_SERVICE", "ge-qualify-agent")
    assert ge_auth.auth_mode() == "enforce"
    monkeypatch.setenv("A2A_AUTH_MODE", "bogus")
    assert ge_auth.auth_mode() == "enforce"


# --- middleware ------------------------------------------------------------


def _app() -> TestClient:
    async def ok(_request: Any) -> PlainTextResponse:
        return PlainTextResponse("agent")

    app = Starlette(
        routes=[
            Route("/", ok, methods=["POST"]),
            Route("/auth", ok, methods=["GET"]),
            Route("/.well-known/agent-card.json", ok, methods=["GET"]),
        ]
    )
    app.add_middleware(ge_auth.GeminiEnterpriseAuthMiddleware)
    return TestClient(app)


def test_enforce_blocks_a2a_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_AUTH_MODE", "enforce")
    resp = _app().post("/", json={})
    assert resp.status_code == 401 and resp.json() == {"error": "unauthorized"}


def test_enforce_allows_ge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_AUTH_MODE", "enforce")
    resp = _app().post("/", json={}, headers={"X-Serverless-Authorization": f"Bearer {_token()}"})
    assert resp.status_code == 200 and resp.text == "agent"


def test_browser_routes_and_card_stay_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_AUTH_MODE", "enforce")
    client = _app()
    assert client.get("/auth").status_code == 200
    assert client.get("/.well-known/agent-card.json").status_code == 200


def test_log_mode_never_blocks_and_never_logs_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("A2A_AUTH_MODE", "log")
    token = _token(aud="https://wrong.example")
    with caplog.at_level("INFO", logger="qualify.agent.ge_auth"):
        resp = _app().post("/", json={}, headers={"X-Serverless-Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "reason=bad_audience" in caplog.text
    assert "aud=https://wrong.example" in caplog.text
    assert token not in caplog.text
    assert token.split(".")[2] not in caplog.text
