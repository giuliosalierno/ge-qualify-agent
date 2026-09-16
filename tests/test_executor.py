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
    Task,
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
