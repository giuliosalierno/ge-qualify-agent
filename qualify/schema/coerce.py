"""Turning what the widget wrote into what the record holds.

Every A2UI input writes a **string**. `TextField` with `variant: number` still
sends `"10"`; a single-select bound to `CapabilityLevel` sends `"6"`. The
record wants an `int`, a `date`, a `bool` or an `IntEnum`. Something has to
bridge that, and doing it inline at each call site is how one branch ends up
accepting `"maybe"` as a boolean.

Two rules shape this module.

**Empty means unanswered, not zero.** A user who skips a number field sends
`""`. Coercing that to `0` would read as "we measured no saving", which is a
claim nobody made. It becomes `None`, matching the Zero Extrapolation Rule the
original skills were built around.

**Failure is loud.** A value that will not coerce raises rather than being
dropped. Silently discarding it would leave the user looking at a field they
filled in and a record that never received it — the exact failure mode the
pack loader exists to prevent at the other end of the pipeline.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import IntEnum
from typing import Any

from pydantic import BaseModel

from qualify.schema.paths import ResolvedPath, resolve_record_path
from qualify.schema.use_case_record import UseCaseRecord, assert_agent_writable

_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}


class CoercionError(ValueError):
    """Raised when a submitted value cannot become the record's type."""


def _is_empty(raw: Any) -> bool:
    """True when the user left this blank.

    Written as a function rather than `raw in _EMPTY` because a set lookup
    hashes its argument, and `[] in _EMPTY` raises `TypeError` rather than
    returning False. Every list field would have crashed on the way in.

    `0` and `False` are not empty. A user who answers "zero people" has
    answered.

    Nor is `"unknown"`. When a pack offers "Not sure yet" as an option,
    choosing it is an answer — it says the user considered the question and
    does not know. That is worth more than a blank, and conflating the two
    would throw the distinction away.
    """
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip() == ""
    if isinstance(raw, (list, tuple, dict, set)):
        return len(raw) == 0
    return False


def coerce_value(resolved: ResolvedPath, raw: Any) -> Any:
    """Converts one submitted value to the type its record field expects.

    Returns `None` for anything the user left blank, so the caller can tell
    "skipped" from "answered with a falsy value" — `0` hours saved and an
    unfilled hours field are very different things.
    """
    if resolved.is_list:
        return _coerce_list(resolved, raw)

    if isinstance(raw, str):
        raw = raw.strip()
    if _is_empty(raw):
        return None

    target = resolved.scalar_type

    if resolved.is_enum:
        return _coerce_enum(resolved, raw)
    if target is bool:
        return _coerce_bool(resolved, raw)
    if target is int:
        return _coerce_int(resolved, raw)
    if target is float:
        return _coerce_float(resolved, raw)
    if target is date:
        return _coerce_date(resolved, raw)
    if target is str:
        return str(raw)

    # Anything else is a schema change nobody taught this module about.
    # Refusing beats guessing.
    raise CoercionError(
        f"{resolved.path}: no coercion rule for {target.__name__}. "
        f"Add one to qualify.schema.coerce."
    )


def _coerce_list(resolved: ResolvedPath, raw: Any) -> list[str]:
    """List fields come from `ChoicePicker`, which writes a string array."""
    if _is_empty(raw):
        return []
    if isinstance(raw, str):
        # A lone string is tolerated because a renderer collapsing a
        # single-item array is a plausible future behaviour, and losing the
        # user's one selection over it would be a poor trade.
        return [raw] if raw.strip() else []
    if not isinstance(raw, list):
        raise CoercionError(
            f"{resolved.path}: expected a list of strings, got "
            f"{type(raw).__name__}."
        )
    return [str(item) for item in raw if str(item).strip()]


def _coerce_enum(resolved: ResolvedPath, raw: Any) -> IntEnum:
    """Single-selects send the member number as a string, e.g. `"6"`.

    The member *name* is accepted too. Nothing emits it today, but a future
    pack or a hand-written test is likely to, and rejecting `"HIGH_CODE_AGENT"`
    would be a surprising failure.
    """
    enum_cls: Any = resolved.scalar_type

    if isinstance(raw, enum_cls):
        return raw

    text = str(raw)
    try:
        return enum_cls(int(text))
    except (ValueError, TypeError):
        pass

    try:
        return enum_cls[text]
    except KeyError:
        pass

    valid = ", ".join(f"{m.value}={m.name}" for m in enum_cls)
    raise CoercionError(
        f"{resolved.path}: {raw!r} is not a {enum_cls.__name__}. Valid: {valid}"
    )


def _coerce_bool(resolved: ResolvedPath, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise CoercionError(f"{resolved.path}: {raw!r} is not a yes/no value.")


def _coerce_int(resolved: ResolvedPath, raw: Any) -> int:
    value = _coerce_float(resolved, raw)
    if value != int(value):
        raise CoercionError(
            f"{resolved.path}: {raw!r} is not a whole number. "
            f"Counts of people and tasks cannot be fractional."
        )
    return int(value)


def _coerce_float(resolved: ResolvedPath, raw: Any) -> float:
    if isinstance(raw, bool):
        raise CoercionError(f"{resolved.path}: expected a number, got a boolean.")
    if isinstance(raw, (int, float)):
        return float(raw)
    # Thousands separators arrive from users typing "1,200". Stripping them is
    # safer than rejecting, because the alternative is the user retyping a
    # number they already got right.
    text = str(raw).replace(",", "").strip()
    try:
        return float(text)
    except ValueError as exc:
        raise CoercionError(f"{resolved.path}: {raw!r} is not a number.") from exc


def _coerce_date(resolved: ResolvedPath, raw: Any) -> date:
    if isinstance(raw, date):
        return raw
    text = str(raw)
    try:
        # `DateTimeInput` sends ISO 8601, sometimes with a time component.
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise CoercionError(
            f"{resolved.path}: {raw!r} is not an ISO date (YYYY-MM-DD)."
        ) from exc


# ---------------------------------------------------------------------------
# Writing into the record
# ---------------------------------------------------------------------------


def set_by_path(record: UseCaseRecord, path: str, value: Any) -> None:
    """Assigns `value` at `path`, enforcing ownership on the way in.

    The ownership check runs here rather than at the caller because this is
    the single chokepoint every agent-side write passes through. A rule
    enforced at one chokepoint holds; a rule enforced at four call sites holds
    until someone adds a fifth.

    `validate_assignment=True` on the models means pydantic re-validates each
    write, so a negative user count fails here rather than at serialisation.
    """
    assert_agent_writable(path)

    segments = [s for s in path.replace(".", "/").split("/") if s]
    if segments and segments[0] == "uc":
        segments = segments[1:]
    if not segments:
        raise CoercionError(f"{path!r} names no field.")

    target: Any = record
    for segment in segments[:-1]:
        target = getattr(target, segment)
        if not isinstance(target, BaseModel):
            raise CoercionError(f"{path!r} passes through a non-container.")

    setattr(target, segments[-1], value)


def coerce_and_set(record: UseCaseRecord, path: str, raw: Any) -> Any:
    """Coerces then assigns. Returns the stored value."""
    value = coerce_value(resolve_record_path(path), raw)
    set_by_path(record, path, value)
    return value


def get_by_path(record: UseCaseRecord, path: str) -> Any:
    """Reads the value at `path`."""
    segments = [s for s in path.replace(".", "/").split("/") if s]
    if segments and segments[0] == "uc":
        segments = segments[1:]

    target: Any = record
    for segment in segments:
        target = getattr(target, segment)
    return target
