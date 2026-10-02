"""Shared pytest configuration.

The SharePoint connect card intercepts the opening turn of a conversation,
which shifts every existing interview test by one turn. Those tests are about
the qualification loop, not about auth, so the card is off by default here and
switched on explicitly by `test_a2ui_signin.py`.

Making this a default rather than editing each test keeps the coupling in one
visible place: if the card's trigger conditions change, this file is where the
suite says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_signin_card_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNIN_CARD", "0")


@pytest.fixture(autouse=True)
def _disable_welcome_menu_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same reasoning as the sign-in card: the suite opens interviews with
    "hello" and expects Stage 1 back. `test_welcome_menu.py` switches it on.
    """
    monkeypatch.setenv("WELCOME_MENU", "0")


@pytest.fixture(autouse=True)
def _isolate_sharepoint_mock_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Points the SharePoint mock at a fresh directory for every test.

    Without this the connector falls back to `.data/sharepoint_mock`, a
    gitignored directory that accumulates folders from local runs. A test that
    lists opportunities then passes or fails according to what happens to be on
    the developer's laptop, and reads as green on a machine where the directory
    is empty — which is every CI machine.

    This surfaced when the pending-review listing started reading the mock: the
    suite reported three opportunities that exist nowhere in the repository.
    """
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path / "sharepoint_mock"))
    monkeypatch.delenv("QUALIFY_GCS_BUCKET", raising=False)
    # Same reasoning for the Google Drive mock, and a developer's shell
    # exporting STORAGE_PROVIDER must not silently switch the suite's provider.
    monkeypatch.setenv("GDRIVE_MOCK_DIR", str(tmp_path / "gdrive_mock"))
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)


@pytest.fixture(autouse=True)
def _disable_interactive_views_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the existing suite on the markdown replies it asserts against.

    The side-panel views replace long chat replies with a short headline, so
    every test that checks report text would need rewriting. They stay on the
    markdown path here; `test_views.py` switches the views on explicitly, the
    same arrangement as the sign-in card above.
    """
    monkeypatch.setenv("INTERACTIVE_VIEWS", "0")
