"""Tests for the A2A Agent Card and QualifyAgentExecutor."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    DataPart,
    Message,
    MessageSendParams,
    Part,
    Role,
    TaskState,
    TextPart,
)

from qualify.agent.card import WIRE_VERSION, build_agent_card
from qualify.agent.executor import QualifyAgentExecutor
from qualify.a2ui.catalog import catalog_id
from qualify.sinks.session import InMemorySessionStore

NEEDS_PAYLOAD = {
    "meta": {"initiative_name": "Claims triage"},
    "business": {
        "problem_description": "Handlers re-key claims by hand from email.",
        "user_profile": "Claims handler",
        "user_count": "12",
    },
}


class MockEventQueue(EventQueue):
    """Collects events enqueued during executor execution."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture
def agent_card():
    return build_agent_card("http://test-server:8080")


def test_agent_card_has_a2ui_extension(agent_card) -> None:
    assert agent_card.capabilities is not None
    assert agent_card.capabilities.streaming is True
    assert agent_card.capabilities.extensions is not None

    ext = agent_card.capabilities.extensions[0]
    assert ext.uri == "https://a2ui.org/a2a-extension/a2ui/v0.9"
    assert ext.params["supportedCatalogIds"] == [catalog_id()]


def test_executor_handles_initial_text_turn(agent_card) -> None:
    async def _test():
        store = InMemorySessionStore(quiet=True)
        executor = QualifyAgentExecutor(agent_card, session_store=store)

        message = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text="Hello, I want to qualify claims triage"))],
            context_id="ctx-exec-1",
            message_id="msg-1",
        )
        context = RequestContext(
            request=MessageSendParams(message=message),
            context_id="ctx-exec-1",
        )
        queue = MockEventQueue()

        await executor.execute(context, queue)

        assert len(queue.events) > 0
        last_event = queue.events[-1]
        assert hasattr(last_event, "status")
        # `completed` is the only state Gemini Enterprise renders properly.
        # It is terminal, which would normally stop the A2UI buttons on this
        # card from ever dispatching; ReopenableTaskStore handles that without
        # changing what goes on the wire. See tests/test_task_lifecycle.py.
        assert last_event.status.state == TaskState.completed
        assert last_event.status.message is not None

        parts = last_event.status.message.parts
        assert len(parts) == 4
        assert isinstance(parts[0].root, TextPart)
        assert "Problem and users" in parts[0].root.text

        for p in parts[1:]:
            assert isinstance(p.root, DataPart)
            assert p.root.data.get("version") == WIRE_VERSION

    asyncio.run(_test())


def test_executor_handles_commit_stage_action(agent_card) -> None:
    async def _test():
        store = InMemorySessionStore(quiet=True)
        executor = QualifyAgentExecutor(agent_card, session_store=store)

        init_msg = Message(
            role=Role.user,
            parts=[Part(root=TextPart(text="Start"))],
            context_id="ctx-exec-2",
            message_id="msg-init",
        )
        init_ctx = RequestContext(
            request=MessageSendParams(message=init_msg),
            context_id="ctx-exec-2",
        )
        await executor.execute(init_ctx, MockEventQueue())

        action_part = Part(
            root=DataPart(
                data={
                    "version": WIRE_VERSION,
                    "action": {
                        "name": "commit_stage",
                        "context": NEEDS_PAYLOAD,
                    },
                }
            )
        )
        action_msg = Message(
            role=Role.user,
            parts=[action_part],
            context_id="ctx-exec-2",
            message_id="msg-action",
        )
        action_ctx = RequestContext(
            request=MessageSendParams(message=action_msg),
            context_id="ctx-exec-2",
        )
        queue = MockEventQueue()

        await executor.execute(action_ctx, queue)

        assert len(queue.events) > 0
        last_event = queue.events[-1]
        parts = last_event.status.message.parts

        assert isinstance(parts[0].root, TextPart)
        assert "Effort and value" in parts[0].root.text

        session = store.load("ctx-exec-2")
        assert session is not None
        assert session.active_stage == 1
        assert session.record.business.user_count == 12

    asyncio.run(_test())


def _text_context(context_id: str, text: str, message_id: str) -> RequestContext:
    message = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
        context_id=context_id,
        message_id=message_id,
    )
    return RequestContext(
        request=MessageSendParams(message=message), context_id=context_id
    )


def test_turns_run_off_the_event_loop_and_serialize_per_context(
    agent_card, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow turn must not stall other conversations, and two events in the
    same conversation must not run at the same time.

    The fake turn sleeps synchronously, like a blocking Gemini call. Called on
    the event loop it would serialize everything; on a worker thread only the
    same-context pair should wait for each other.
    """
    import threading
    import time

    from qualify.agent import executor as executor_mod
    from qualify.agent.turn import TurnOutput

    spans: dict[str, tuple[float, float]] = {}
    spans_lock = threading.Lock()

    def slow_turn(store, turn_input, **_kwargs):
        start = time.monotonic()
        time.sleep(0.3)
        end = time.monotonic()
        with spans_lock:
            spans[turn_input.user_text] = (start, end)
        return TurnOutput(reply_text="ok", a2ui_messages=[], session=None)

    monkeypatch.setattr(executor_mod, "execute_turn", slow_turn)

    async def _test():
        executor = QualifyAgentExecutor(
            agent_card, session_store=InMemorySessionStore(quiet=True)
        )
        await asyncio.gather(
            executor.execute(_text_context("ctx-a", "a1", "m1"), MockEventQueue()),
            executor.execute(_text_context("ctx-a", "a2", "m2"), MockEventQueue()),
            executor.execute(_text_context("ctx-b", "b1", "m3"), MockEventQueue()),
        )
        return executor

    executor = asyncio.run(_test())

    def overlap(x: str, y: str) -> bool:
        (s1, e1), (s2, e2) = spans[x], spans[y]
        return s1 < e2 and s2 < e1

    assert set(spans) == {"a1", "a2", "b1"}
    assert not overlap("a1", "a2"), "same conversation must be serialized"
    assert overlap("a1", "b1") or overlap("a2", "b1"), (
        "different conversations must run concurrently"
    )

    # Locks are weakly held: nothing lingers once the turns are done.
    import gc

    gc.collect()
    assert len(executor._context_locks) == 0
