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
