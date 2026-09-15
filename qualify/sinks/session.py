"""Holding an in-progress interview between turns.

Gemini Enterprise opens a **new `taskId` for every chat message** but keeps
`contextId` stable for the whole conversation, including across a full browser
reload (L3, verified in Phase 0). So `contextId` is the only durable handle on
an interview, and it is what this store keys on (D15).

.. warning::

   `InMemorySessionStore` is correct on one process and wrong on two.

   Cloud Run scales horizontally by default. The user's next message can land
   on a different instance, which has never heard of their `contextId`, and
   the interview restarts from stage one with an empty record. The user sees
   their answers vanish; the logs show a normal new session. Nothing errors.

   That silence is what makes it dangerous, so it is tracked as a limitation
   rather than left as folklore. Until the GCS-backed store lands in Phase 3,
   deploy with ``--max-instances=1``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from qualify.packs.loader import Pack, load_pack
from qualify.schema.use_case_record import Meta, UseCaseRecord

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Session:
    """One user's progress through one pack.

    Holds the stage index rather than the stage id because advancing is then
    arithmetic rather than a lookup that can fail. The id is still available
    via :attr:`stage`.
    """

    context_id: str
    pack_name: str
    record: UseCaseRecord
    active_stage: int = 0
    #: Stage indices the user has committed. Kept as a set rather than a
    #: high-water mark because `revise_stage` can reopen stage 1 while stages
    #: 2 and 3 stay committed, and a single integer cannot express that.
    committed: set[int] = field(default_factory=set)
    #: Stage indices whose initial surface has already been emitted to the client.
    rendered_stages: set[int] = field(default_factory=set)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @property
    def pack(self) -> Pack:
        """The loaded pack. Cached upstream by `load_pack`, so this is cheap."""
        return load_pack(self.pack_name)

    @property
    def stage(self) -> str:
        """The active stage's id."""
        return self.pack.stages[self.active_stage].id

    @property
    def is_last_stage(self) -> bool:
        return self.active_stage >= len(self.pack.stages) - 1

    @property
    def is_complete(self) -> bool:
        """Every stage committed. The precondition for `finalize`."""
        return len(self.committed) == len(self.pack.stages)

    def advance(self) -> bool:
        """Marks the active stage committed and moves on.

        Returns False on the last stage, where there is nowhere to advance
        to. The caller should treat that as "ready to finalise", not as an
        error — running off the end of the pack is the goal, not a fault.
        """
        self.committed.add(self.active_stage)
        self.touch()
        if self.is_last_stage:
            return False
        self.active_stage += 1
        return True

    def reopen(self, stage_idx: int) -> None:
        """Backs up to an earlier stage without discarding later answers.

        Later stages stay in `committed`. A user correcting a typo in stage 1
        should not have to re-confirm stages 2 and 3, and the field-level
        `user_confirmed` marks are what actually protect their data.
        """
        if not 0 <= stage_idx < len(self.pack.stages):
            raise IndexError(
                f"stage {stage_idx} is outside pack {self.pack_name!r} "
                f"(0-{len(self.pack.stages) - 1})"
            )
        self.committed.discard(stage_idx)
        self.rendered_stages.discard(stage_idx)
        self.active_stage = stage_idx
        self.touch()

    def touch(self) -> None:
        self.updated_at = _now()


class SessionStore(Protocol):
    """The seam the GCS store slots into in Phase 3.

    Narrow on purpose. Anything richer — queries, listings, partial updates —
    would be shaped by what the in-memory version makes easy rather than by
    what the real backend can do.
    """

    def load(self, context_id: str) -> Session | None: ...

    def save(self, session: Session) -> None: ...

    def delete(self, context_id: str) -> None: ...


class InMemorySessionStore:
    """A dict. Adequate for tests and a single-instance deployment.

    Warns on construction rather than relying on the module docstring. A
    hazard recorded only in prose is one nobody reads at deploy time, and
    this one is invisible afterwards — see L13. Putting it in the startup
    logs places it next to the deploy command that caused it.

    Pass ``quiet=True`` in tests, where the warning is noise.
    """

    def __init__(self, quiet: bool = False) -> None:
        self._sessions: dict[str, Session] = {}
        if not quiet:
            log.warning(
                "Using InMemorySessionStore: interviews are held in process "
                "memory and will be lost if this service runs more than one "
                "instance. Deploy with --max-instances=1 until the shared "
                "store lands (L13)."
            )

    def load(self, context_id: str) -> Session | None:
        return self._sessions.get(context_id)

    def save(self, session: Session) -> None:
        session.touch()
        self._sessions[session.context_id] = session

    def delete(self, context_id: str) -> None:
        self._sessions.pop(context_id, None)

    def __len__(self) -> int:
        return len(self._sessions)


def new_session(
    context_id: str, pack_name: str = "business", record_id: str | None = None
) -> Session:
    """Starts an interview.

    `context_id` is written into the record's meta as well as being the store
    key, so a record recovered from the sink alone can still be traced back to
    its conversation.
    """
    load_pack(pack_name)  # fail fast on a bad pack name, before any state exists
    return Session(
        context_id=context_id,
        pack_name=pack_name,
        record=UseCaseRecord(
            meta=Meta(
                record_id=record_id or f"uc-{context_id}",
                context_id=context_id,
            )
        ),
    )


def get_or_start(
    store: SessionStore, context_id: str, pack_name: str = "business"
) -> Session:
    """Loads the interview for this conversation, or begins one.

    Every turn goes through here, so the "have we met?" decision is made in
    one place rather than at each entry point.
    """
    session = store.load(context_id)
    if session is None:
        session = new_session(context_id, pack_name)
        store.save(session)
    return session
