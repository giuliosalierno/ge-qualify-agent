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

import pytest


@pytest.fixture(autouse=True)
def _disable_signin_card_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNIN_CARD", "0")
