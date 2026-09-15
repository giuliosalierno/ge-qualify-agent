"""Applying a `commit_stage` payload to the record, and gating the stage.

Phase 0 reshaped this module before it was written. The original plan assumed
`sendDataModel` echoed the surface on every turn, so provenance could be
computed by diffing what the agent believed against what the user saw. It does
not echo (L1). The only inbound path is the `commit_stage` action.

So provenance is **event-driven, not diff-driven**. It runs when the user
presses Continue, and at no other time.

That has a consequence worth stating plainly: between two commits, the record
and the screen can disagree and the agent cannot tell. The record is
authoritative only up to the last commit. Everything after it is provisional.

Three rules, all enforced here rather than trusted to callers:

1. **A field in a commit payload becomes `user_confirmed`.** The user saw it
   on screen and pressed Continue. That is confirmation, whether they typed
   the value or the agent drafted it.
2. **`user_confirmed` is write-once.** The extractor can re-derive an old
   value from earlier conversation, and without this guard it would silently
   undo a manual correction. Only an explicit `revise_stage` reopens a field.
3. **A stage does not close while a required field is blank.** The base
   `Button` has no `disabled` prop (L11), so there is no client-side gate.
   This is the only gate, which makes it the one that has to work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qualify.packs.loader import FieldSpec, Pack, Stage
from qualify.schema.coerce import CoercionError, coerce_and_set, get_by_path
from qualify.schema.paths import resolve_record_path
from qualify.schema.use_case_record import OwnershipError, UseCaseRecord


@dataclass
class FieldChange:
    """One field the commit moved."""

    path: str
    before: Any
    after: Any

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass
class CommitResult:
    """What a `commit_stage` did, and whether the stage may close.

    Deliberately a report rather than an exception. A commit with two blank
    required fields and one bad number is not an error to be raised — it is a
    normal state the agent has to talk the user through, and it needs all
    three problems at once, not the first one.
    """

    stage_id: str
    changes: list[FieldChange] = field(default_factory=list)
    #: Required fields still blank after the commit.
    missing: list[FieldSpec] = field(default_factory=list)
    #: `(path, message)` for values that would not coerce.
    rejected: list[tuple[str, str]] = field(default_factory=list)
    #: Paths the agent tried to write but does not own. Should never be
    #: non-empty; if it is, a pack or a caller has a bug worth shouting about.
    refused: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the stage may advance."""
        return not (self.missing or self.rejected or self.refused)

    @property
    def confirmed_paths(self) -> list[str]:
        return [c.path for c in self.changes]

    def blocking_summary(self) -> str:
        """One human sentence naming what is still outstanding.

        Written here rather than in the agent prompt so the wording is
        testable and cannot drift per turn.
        """
        parts = []
        if self.missing:
            labels = ", ".join(f.label for f in self.missing)
            parts.append(f"still needed: {labels}")
        for _, message in self.rejected:
            parts.append(message)
        for path, message in self.refused:
            parts.append(f"{path} is not ours to write ({message})")
        return "; ".join(parts)


def extract_stage_values(
    stage: Stage, payload: dict[str, Any]
) -> dict[str, Any]:
    """Pulls this stage's fields out of a commit payload.

    The Continue button binds `{"path": "/uc"}`, so GE returns the **entire**
    record subtree, not just the active stage. Verified in Phase 0.

    Taking only the active stage's fields is a deliberate narrowing. The
    payload also carries stage 1's answers on a stage 3 commit, and treating
    those as freshly confirmed would mark fields the user has not looked at
    since. Confirmation should mean "I just saw this", not "it was on screen
    once".
    """
    values: dict[str, Any] = {}
    payload = _unwrap_root(payload)
    for spec in stage.fields:
        if spec.readonly:
            continue
        value = _dig(payload, spec.path)
        if value is not _ABSENT:
            values[spec.path] = value
    return values


class _Absent:
    """Distinguishes "key not in payload" from "key present and empty"."""


_ABSENT = _Absent()


def _unwrap_root(payload: dict[str, Any]) -> dict[str, Any]:
    """Strips a `uc` wrapper if the payload arrived with one.

    The Continue button binds `{"path": "/uc"}` and GE returns the resolved
    subtree, so the top-level keys are normally `business`, `meta` and so on.
    But a caller binding `/` instead would send the whole model, and the
    failure would be silent: every field would look absent, the stage would
    report four blanks, and nothing would say why.

    Unwrapping is unambiguous because no record section is named `uc`.
    """
    if list(payload.keys()) == ["uc"] and isinstance(payload["uc"], dict):
        return payload["uc"]
    return payload


def _dig(payload: dict[str, Any], path: str) -> Any:
    """Walks a JSON Pointer into the payload.

    The `/uc` prefix is dropped from the path, since the payload is the `/uc`
    subtree and that segment is already consumed. Call `_unwrap_root` first
    if the payload itself might still carry the wrapper.
    """
    segments = [s for s in path.split("/") if s]
    if segments and segments[0] == "uc":
        segments = segments[1:]

    node: Any = payload
    for segment in segments:
        if not isinstance(node, dict) or segment not in node:
            return _ABSENT
        node = node[segment]
    return node


def apply_commit(
    record: UseCaseRecord, pack: Pack, stage_idx: int, payload: dict[str, Any]
) -> CommitResult:
    """Writes a stage's submitted values into the record and reports the gate.

    Collects every problem rather than stopping at the first. A user who left
    two fields blank and mistyped a third deserves to hear all three in one
    turn, not to fix them one round trip at a time.
    """
    stage = pack.stages[stage_idx]
    result = CommitResult(stage_id=stage.id)
    submitted = extract_stage_values(stage, payload)

    for spec in stage.fields:
        if spec.readonly or spec.path not in submitted:
            continue

        before = get_by_path(record, spec.path)
        try:
            after = coerce_and_set(record, spec.path, submitted[spec.path])
        except CoercionError as exc:
            result.rejected.append((spec.path, str(exc)))
            continue
        except OwnershipError as exc:
            result.refused.append((spec.path, str(exc)))
            continue

        result.changes.append(FieldChange(spec.path, before, after))

        # Presence in the payload is the confirmation. The user saw the field
        # and pressed Continue. A blank stays unconfirmed so the gate below
        # can still catch it.
        if after is not None and after != [] and after != "":
            record.mark(resolve_record_path(spec.path).dotted, "user_confirmed")

    result.missing = missing_required(record, stage)
    return result


def missing_required(record: UseCaseRecord, stage: Stage) -> list[FieldSpec]:
    """Required fields of `stage` that are still blank.

    Checks the record rather than the payload so a value confirmed on an
    earlier attempt still counts. Otherwise a user who fixed one field would
    be told the other three are missing again.
    """
    out: list[FieldSpec] = []
    for spec in stage.fields:
        if not spec.required or spec.readonly:
            continue
        value = get_by_path(record, spec.path)
        if value is None or value == "" or value == []:
            out.append(spec)
    return out


def draft(record: UseCaseRecord, path: str, raw: Any) -> bool:
    """Writes an extracted value, unless the user already confirmed the field.

    This is the guard behind watch item 2 in the plan. The extractor re-reads
    the whole conversation each turn, so it will happily re-derive the value a
    user corrected two turns ago. Without this, every manual fix has a
    lifetime of one turn.

    Returns True if the write happened.
    """
    resolved = resolve_record_path(path)
    if record.provenance_of(resolved.dotted) == "user_confirmed":
        return False

    coerce_and_set(record, path, raw)
    record.mark(resolved.dotted, "agent_draft")
    return True


def unconfirmed_in_stage(record: UseCaseRecord, stage: Stage) -> list[FieldSpec]:
    """Fields the agent drafted but the user has not confirmed.

    The acceptance criterion for Phase 2 is that no stage advances with an
    `agent_draft` field outstanding. This is what that check reads.
    """
    return [
        spec
        for spec in stage.fields
        if not spec.readonly
        and record.provenance_of(resolve_record_path(spec.path).dotted)
        == "agent_draft"
    ]
