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


def test_turn_reports_completed_on_the_wire(client: TestClient) -> None:
    """`completed` is the only state Gemini Enterprise renders properly.

    `input_required` makes GE discard the agent's UI and show its own approval
    widget instead ("Review: Mock Function Call For Required User Input").
    `auth_required` renders nothing at all. Both were tried in production.
    """
    results, errors = _stream(
        client, _envelope([{"kind": "text", "text": "hello"}], "ctx-open")
    )

    assert not errors
    assert _state_of(results) == "completed"


def test_button_click_against_a_completed_task_is_accepted(client: TestClient) -> None:
    """The production failure, and the thing the task store exists to fix.

    GE sends a button click carrying the taskId of the task that drew the
    button. That task is `completed`, and the SDK rejects follow-ups to a
    terminal task with -32602 before the executor runs — which surfaced as a
    bare "Something went wrong" with nothing in the server logs.
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
    assert _state_of(results) == "completed"


def test_several_clicks_in_a_row_all_land(client: TestClient) -> None:
    """One reopening must not be a one-off.

    An interview is four stages of buttons, so the task gets reopened on every
    single turn.
    """
    opening, _ = _stream(
        client, _envelope([{"kind": "text", "text": "hello"}], "ctx-many")
    )
    task_id = next(r["id"] for r in opening if r.get("kind") == "task")

    for attempt in range(3):
        _results, errors = _stream(
            client,
            _envelope(
                [{"kind": "text", "text": "Skip"}, _SKIP_PART],
                "ctx-many",
                task_id=task_id,
            ),
        )
        assert not errors, f"click {attempt + 1} rejected: {errors}"


def test_without_the_wrapper_the_click_is_rejected() -> None:
    """Proves the wrapper is load-bearing rather than decorative.

    Builds the same app with a plain InMemoryTaskStore and shows the -32602 that
    users actually hit.
    """
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore

    from qualify.agent.card import build_agent_card
    from qualify.agent.executor import QualifyAgentExecutor
    from qualify.sinks.session import InMemorySessionStore

    card = build_agent_card("http://testserver")
    handler = DefaultRequestHandler(
        agent_executor=QualifyAgentExecutor(
            agent_card=card, session_store=InMemorySessionStore(quiet=True)
        ),
        task_store=InMemoryTaskStore(),  # deliberately unwrapped
    )
    bare = TestClient(A2AStarletteApplication(agent_card=card, http_handler=handler).build())

    opening, _ = _stream(bare, _envelope([{"kind": "text", "text": "hello"}], "ctx-bare"))
    task_id = next(r["id"] for r in opening if r.get("kind") == "task")

    _results, errors = _stream(
        bare,
        _envelope([{"kind": "text", "text": "Skip"}, _SKIP_PART], "ctx-bare", task_id=task_id),
    )

    assert errors, "expected the terminal-state rejection without the wrapper"
    assert errors[0]["code"] == -32602
    assert "terminal state" in errors[0]["message"]
