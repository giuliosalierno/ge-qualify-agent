"""Regressions found by the end-to-end demo run (real Gemini, real GCS)."""

from __future__ import annotations

import pytest

from qualify.agent.handover import format_source_names
from qualify.agent.turn import TurnInput, execute_turn
from qualify.sinks.session import InMemorySessionStore, new_session


class _ClaimsDoneChat:
    """A chat model that calls the stage complete whatever the card says."""

    def reply(self, **_kwargs) -> str:
        return "All required fields are captured. Please click **Continue** on the card."


def test_source_names_use_the_pack_labels() -> None:
    """'sap' rendered as 'Sap' in the tech-review summary."""
    assert format_source_names(["sap", "workspace_mail"]) == "SAP, Email or calendar"


def test_chat_cannot_invite_continue_while_a_required_field_is_empty() -> None:
    """The model said 'all fields captured' while model_profile was empty."""
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-e2e-claim",
            user_text="Our AP clerks re-key supplier invoices into SAP by hand every day",
        ),
        chat_client=_ClaimsDoneChat(),
    )
    assert "Still needed on the card before **Continue**" in out.reply_text
    assert "Initiative name" in out.reply_text


def test_next_steps_after_completion_do_not_mention_sharepoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "none")
    store = InMemorySessionStore(quiet=True)
    session = new_session("ctx-e2e-done", "business")
    for idx in range(len(session.pack.stages)):
        session.skipped.add(idx)
        session.committed.add(idx)
    session.active_stage = len(session.pack.stages) - 1
    store.save(session)
    out = execute_turn(store, TurnInput(context_id="ctx-e2e-done", user_text="what next?"))
    assert "portfolio review" in out.reply_text
    assert "SharePoint" not in out.reply_text


def test_use_case_typed_first_opens_intake_with_a_short_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full menu text used to sit in front of the Stage 1 question."""
    monkeypatch.setenv("WELCOME_MENU", "1")
    store = InMemorySessionStore(quiet=True)
    out = execute_turn(
        store,
        TurnInput(
            context_id="ctx-e2e-first",
            user_text="we need search over our contracts so legal can answer questions faster",
        ),
    )
    assert out.reply_text.startswith("📋 Starting a **Business Value Intake**")
    assert "I support three workflows" not in out.reply_text
