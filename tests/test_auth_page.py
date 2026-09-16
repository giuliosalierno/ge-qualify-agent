"""Tests that render the `/auth` page.

These exist because a `NameError` in the page template reached production. The
suite had thorough coverage of the OAuth *logic* and none at all of the HTML it
is served in, so removing a variable still referenced by an f-string was
invisible until a user hit a 500.

Rendering the page is cheap. Anything that can 500 on a real request should be
exercised here.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from qualify.mcp.sharepoint_mcp import handle_oauth_auth

_TENANT = "918002ad-54bb-4139-804a-2da0d762bd54"
_CLIENT = "d8a18018-c2a9-4f7e-b464-320141d6623d"


@pytest.fixture(autouse=True)
def _entra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page only renders its full form when Entra is configured."""
    monkeypatch.setenv("MS_GRAPH_TENANT_ID", _TENANT)
    monkeypatch.setenv("MS_GRAPH_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("AGENT_URL", "https://agent.example.run.app")


@pytest.fixture(autouse=True)
def _no_device_code_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs the device code call so the page renders without touching Entra."""
    monkeypatch.setattr(
        "qualify.connectors.sharepoint.start_device_code_flow_for_session",
        lambda context_id="latest": {
            "user_code": "TEST-CODE",
            "verification_uri": "https://microsoft.com/devicelogin",
        },
    )


def _render(query: str = "context_id=ctx-1") -> str:
    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/auth",
            "query_string": query.encode("utf-8"),
            "headers": [(b"host", b"agent.example.run.app")],
            "server": ("agent.example.run.app", 443),
            "scheme": "https",
        }
    )
    response = asyncio.run(handle_oauth_auth(request))
    assert response.status_code == 200, f"/auth returned {response.status_code}"
    return response.body.decode("utf-8")


def test_auth_page_renders_without_a_template_error() -> None:
    """The regression that shipped: a removed variable still named in the HTML."""
    assert "Connect Microsoft SharePoint Online" in _render()


def test_auth_page_hides_one_click_until_the_callback_is_registered() -> None:
    """A button that returns AADSTS50011 is worse than an honest explanation."""
    html = _render()

    assert "One-click sign-in is not enabled yet" in html
    assert "https://agent.example.run.app/auth/callback" in html
    # Device code still offered, because it works today.
    assert "TEST-CODE" in html


def test_auth_page_offers_one_click_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_OAUTH_CALLBACK", "1")
    html = _render()

    assert "one-click sign-in" in html
    assert "login.microsoftonline.com" in html
    assert "redirect_uri=https%3A%2F%2Fagent.example.run.app%2Fauth%2Fcallback" in html


def test_auth_page_never_points_at_the_gemini_enterprise_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vertexaisearch.cloud.google.com belongs to GE, not to us.

    Sending a user there with a state we generated produces
    "Failed to decrypt the OAuth state parameter" and no way forward. It must
    not reappear in this page in either state.
    """
    assert "vertexaisearch" not in _render()

    monkeypatch.setenv("WEB_OAUTH_CALLBACK", "1")
    assert "vertexaisearch" not in _render()


def test_auth_page_has_no_paste_box_left_behind() -> None:
    """The input is gone, so any JavaScript still reading it would throw."""
    html = _render()
    assert "paste-url" not in html
    assert "exchangePastedCode" not in html
