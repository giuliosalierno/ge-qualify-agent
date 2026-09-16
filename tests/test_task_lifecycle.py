"""Task lifecycle tests, driven through the real A2A HTTP stack.

These exist because 283 unit tests passed while every A2UI button in production
was broken. The unit tests call `execute_turn` directly, so they never see the
part that failed: the A2A SDK rejected button clicks *before* the executor ran.

Observed in production on 2026-09-16, conversation 01abe8e5:

    15:14:18  116ms   200   plain text  -> connect card      OK
    15:14:37  2.44s   200   plain text  -> banner + stage 1  OK
    15:17:26  7.9ms   200   Skip button -> nothing           "Something went wrong"

    -32602  Task <id> is in terminal state: completed

A2UI buttons dispatch against the task that drew them. Every turn ended
`completed`, which is terminal, so no button could ever work. Anything that
verifies this has to go over HTTP with a real `taskId`.
"""

from __future__ import annotations

import json
import uuid

import pytest
from starlette.testclient import TestClient

from qualify.a2ui.actions import SKIP_STAGE

WIRE_VERSION = "v0.9"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Builds the real app with the LLM clients disabled.

    `build_app` falls back to None for both when construction raises, which
    keeps these tests off the network and fast.
    """
    import qualify.agent.server as server_mod

    def _no_llm(*_args, **_kwargs):
        raise RuntimeError("LLM disabled for tests")

    monkeypatch.setattr(server_mod, "GeminiExtractionClient", _no_llm)
    monkeypatch.setattr(server_mod, "GeminiChatClient", _no_llm)
    monkeypatch.setenv("SIGNIN_CARD", "0")
    monkeypatch.delenv("TASK_STATE_COMPAT", raising=False)

    app, _host, _port = server_mod.build_app()
    return TestClient(app)


def _envelope(
    parts: list[dict], context_id: str, task_id: str | None = None
) -> dict:
    message: dict = {
        "kind": "message",
        "role": "user",
        "messageId": str(uuid.uuid4()),
        "contextId": context_id,
        "parts": parts,
    }
    if task_id:
        message["taskId"] = task_id
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/stream",
        "params": {"message": message},
    }


def _stream(client: TestClient, payload: dict) -> tuple[list[dict], list[dict]]:
    """Returns (results, errors) from every SSE frame."""
    results: list[dict] = []
    errors: list[dict] = []
    with client.stream("POST", "/", json=payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            body = json.loads(line[5:])
            if "error" in body:
                errors.append(body["error"])
            else:
                results.append(body.get("result", {}))
    return results, errors


def _state_of(results: list[dict]) -> str | None:
    for res in reversed(results):
        state = (res.get("status") or {}).get("state")
        if state:
            return state
    return None


_SKIP_PART = {
    "kind": "data",
    "data": {
        "version": WIRE_VERSION,
        "action": {
            "name": SKIP_STAGE,
            "context": {"prompt": "Skip — Effort and value", "stage": "effort_value"},
            "surfaceId": "qualify",
        },
    },
}


def test_turn_leaves_the_task_open_for_the_next_click(client: TestClient) -> None:
    """A rendered form means the agent is waiting, not finished."""
    results, errors = _stream(
        client, _envelope([{"kind": "text", "text": "hello"}], "ctx-open")
    )

    assert not errors
    assert _state_of(results) == "input-required"


def test_button_click_against_the_same_task_is_accepted(client: TestClient) -> None:
    """The exact production failure.

    Gemini Enterprise sends a button click with the taskId of the task that drew
    the button. If that task is terminal the SDK rejects it with -32602 and the
    executor never runs, which surfaces as a bare "Something went wrong".
    """
    opening, _ = _stream(
        client, _envelope([{"kind": "text", "text": "hello"}], "ctx-click")
    )
    task_id = next(r["id"] for r in opening if r.get("kind") == "task")

    results, errors = _stream(
        client,
        _envelope(
            [{"kind": "text", "text": "Skip — Effort and value"}, _SKIP_PART],
            "ctx-click",
            task_id=task_id,
        ),
    )

    assert not errors, f"button click rejected: {errors}"
    assert _state_of(results) == "input-required"


def test_compat_flag_restores_the_terminal_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch works, and demonstrates the bug it escapes from.

    With the old behaviour restored, the click is rejected — which is what makes
    this a regression test rather than a description.
    """
    monkeypatch.setenv("TASK_STATE_COMPAT", "1")

    opening, _ = _stream(
        client, _envelope([{"kind": "text", "text": "hello"}], "ctx-compat")
    )
    task_id = next(r["id"] for r in opening if r.get("kind") == "task")
    assert _state_of(opening) == "completed"

    _results, errors = _stream(
        client,
        _envelope(
            [{"kind": "text", "text": "Skip"}, _SKIP_PART],
            "ctx-compat",
            task_id=task_id,
        ),
    )

    assert errors, "expected the terminal-state rejection"
    assert errors[0]["code"] == -32602
    assert "terminal state" in errors[0]["message"]
