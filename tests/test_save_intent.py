"""Tests for how chat text is classified as save, acknowledgement, or neither.

These exist because of a specific production bug. The connect card asks the user
to sign in on the very first turn. The user signed in, typed "signed in", and
the agent wrote an empty "Untitled Initiative" folder to their SharePoint site
and declared the qualification finished.

Two separate mistakes combined:

1. "signed in" was on the list of phrases that mean "save now". That was a fair
   reading when the only reason to say it was after a save attempt demanded a
   login, and it stopped being fair the moment the card moved to turn one.
2. Nothing checked that there was anything to save.

Either fix alone would have hidden the other, so both are covered here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.session import InMemorySessionStore

_GRAPH_ENV_VARS = (
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CLIENT_SECRET",
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_REFRESH_TOKEN",
)


@pytest.fixture(autouse=True)
def _isolate_sharepoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces the connector into mock mode and clears all vaults.

    ``_custom_mock_dir`` is what actually short-circuits the live Graph call, and
    only the constructor sets it — ``SHAREPOINT_MOCK_DIR`` alone still lets the
    connector reach out to graph.microsoft.com and fall back on the 401. The env
    var is set too, for any code path that reads it directly.

    Real Graph credentials in the environment silently promote the connector out
    of mock mode, so they are removed regardless of how the suite was launched.
    """
    import qualify.connectors.sharepoint as sp_mod
    from qualify.connectors.sharepoint import SharePointConnector

    for var in _GRAPH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SHAREPOINT_MOCK_DIR", str(tmp_path))

    sp_mod._CONNECTOR_INSTANCE = SharePointConnector(mock_dir=tmp_path)
    sp_mod._TOKEN_VAULT.clear()
    sp_mod._REFRESH_VAULT.clear()
    sp_mod._PENDING_RECORDS.clear()
    sp_mod._SYNCED_RESULTS.clear()


def test_signed_in_starts_the_interview_instead_of_saving() -> None:
    """The exact message that caused the empty-draft bug must open stage 1."""
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-ack")
    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-ack")

    out = execute_turn(store, TurnInput(context_id="ctx-ack", user_text="signed in"))

    assert "SharePoint connected" in out.reply_text
    assert "Saved to SharePoint" not in out.reply_text
    # Nothing was written and nothing was queued for the post-login auto-sync.
    assert not sp_mod._PENDING_RECORDS
    assert not sp_mod._SYNCED_RESULTS
    # The user is moved forward rather than left staring at a bare confirmation.
    assert out.a2ui_messages
    assert out.session.rendered_stages == {0}


def test_logged_in_is_also_an_acknowledgement() -> None:
    """The other phrasing of the same thing takes the same path."""
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-ack2")
    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-ack2")

    out = execute_turn(store, TurnInput(context_id="ctx-ack2", user_text="logged in"))

    assert "SharePoint connected" in out.reply_text
    assert not sp_mod._SYNCED_RESULTS


def test_claiming_to_be_signed_in_without_a_token_says_so() -> None:
    """Don't take the user's word for it.

    A sign-in fails quietly often enough — closed tab, declined consent, a
    container restart that wiped the vault — that agreeing with them would only
    move the disappointment to save time.
    """
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-ack-notoken")

    out = execute_turn(
        store, TurnInput(context_id="ctx-ack-notoken", user_text="signed in")
    )

    assert "can't see a completed sign-in" in out.reply_text
    assert "✅" not in out.reply_text
    # Still not a dead end: the interview starts anyway.
    assert out.session.rendered_stages == {0}


def test_connected_banner_fires_on_any_message_not_just_signed_in() -> None:
    """The whole point of the banner.

    The sign-in completes in a browser tab that Gemini Enterprise cannot see, so
    the chat stays silent. Whatever the user types next is the first chance to
    tell them it worked — and they will rarely type the words "signed in".
    """
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-banner")
    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-banner")

    out = execute_turn(
        store, TurnInput(context_id="ctx-banner", user_text="let's get started")
    )

    assert "Microsoft SharePoint connected" in out.reply_text
    assert out.session.signin_confirmed is True


def test_connected_banner_is_announced_only_once() -> None:
    """Repeating it every turn would be noise."""
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-banner2")
    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-banner2")

    first = execute_turn(
        store, TurnInput(context_id="ctx-banner2", user_text="hello")
    )
    second = execute_turn(
        store, TurnInput(context_id="ctx-banner2", user_text="hello again")
    )

    assert "Microsoft SharePoint connected" in first.reply_text
    assert "Microsoft SharePoint connected" not in second.reply_text


def test_banner_and_acknowledgement_do_not_both_fire() -> None:
    """Saying "connected" twice in one reply reads like a bug."""
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-both")
    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-both")

    out = execute_turn(store, TurnInput(context_id="ctx-both", user_text="signed in"))

    assert out.reply_text.count("SharePoint connected") == 1


def test_acknowledgement_suppresses_the_connect_card() -> None:
    """Someone who just signed in should never be asked to sign in again."""
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-ack3")

    out = execute_turn(store, TurnInput(context_id="ctx-ack3", user_text="signed in"))

    assert out.session.signin_prompted is True


def test_save_request_on_an_empty_record_is_refused() -> None:
    """An explicit save still needs something to save."""
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    get_or_start(store, "ctx-empty")

    out = execute_turn(
        store, TurnInput(context_id="ctx-empty", user_text="save to sharepoint")
    )

    assert "nothing to save yet" in out.reply_text.lower()
    assert not sp_mod._PENDING_RECORDS
    assert not sp_mod._SYNCED_RESULTS


def test_save_request_with_a_named_initiative_still_saves() -> None:
    """Guards the fix against overcorrecting into "never saves".

    A named initiative is enough content to justify a write, even before any
    stage is committed.
    """
    import qualify.connectors.sharepoint as sp_mod
    from qualify.sinks.session import get_or_start

    store = InMemorySessionStore(quiet=True)
    session = get_or_start(store, "ctx-named")
    session.record.meta.record_id = "UC-2026-778899"
    session.record.meta.initiative_name = "Contract Renewal Triage"
    store.save(session)

    sp_mod.cache_delegated_token("eyJ_live_user_jwt", key="ctx-named")

    out = execute_turn(
        store, TurnInput(context_id="ctx-named", user_text="save to sharepoint")
    )

    assert "Successfully Saved to SharePoint Online" in out.reply_text
    assert "Contract Renewal Triage" in out.reply_text
