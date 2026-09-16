"""A task store that lets a finished conversation continue.

Gemini Enterprise leaves no usable task state for an agent that renders
interactive UI:

======================  ==========================================
`completed`             The A2A SDK refuses any follow-up message:
                        `-32602 Task <id> is in terminal state`.
                        Every A2UI button click is rejected before
                        the executor runs.
`input_required`        GE ignores the agent's own UI and renders its
                        built-in approval widget instead — observed
                        as "Review: Mock Function Call For Required
                        User Input".
`auth_required`         GE renders nothing at all; the turn appears
                        blank.
======================  ==========================================

So the wire state has to stay `completed`, and the terminal-state check has to
stop being a problem some other way.

That check reads the task back from this store. Handing it a copy whose state is
non-terminal satisfies it, the turn proceeds, and the executor closes the turn
as `completed` again. Gemini Enterprise's view never changes; only the SDK's
internal bookkeeping does.

The stored task is never modified — `get` returns a copy — so nothing else that
reads the store sees the rewrite.
"""

from __future__ import annotations

import logging

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types import Task, TaskState

log = logging.getLogger(__name__)

#: States the A2A SDK treats as closed. A message naming a task in any of these
#: is rejected with -32602 before it reaches the agent.
TERMINAL_STATES = frozenset(
    {
        TaskState.completed,
        TaskState.canceled,
        TaskState.failed,
        TaskState.rejected,
    }
)


class ReopenableTaskStore(TaskStore):
    """Wraps a TaskStore and presents finished tasks as still open.

    Only `get` differs from the wrapped store. Saves and deletes pass straight
    through, so what is persisted is always the real state.
    """

    def __init__(self, inner: TaskStore) -> None:
        self._inner = inner

    async def save(self, task: Task, context: ServerCallContext | None = None) -> None:
        await self._inner.save(task, context)

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        task = await self._inner.get(task_id, context)
        if task is None:
            return None
        if task.status.state not in TERMINAL_STATES:
            return task

        # A copy, so the stored task keeps its real state.
        reopened = task.model_copy(deep=True)
        reopened.status.state = TaskState.working
        log.info(
            "Reopening task %s for a follow-up (stored state %s)",
            task_id,
            task.status.state.value,
        )
        return reopened

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        await self._inner.delete(task_id, context)
