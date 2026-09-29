"""Resolving A2UI data-model paths against the `UseCaseRecord`.

The record IS the data model (see `use_case_record` module docstring), which
only holds if every path a pack declares actually exists on the record. This
module is what makes that checkable.

Without it a typo like `/uc/business/problem_descriptoin` compiles into a
perfectly valid A2UI surface that renders an input bound to nothing. The user
types, the value goes into a phantom branch of the data model, and the field
comes back empty on commit. Nothing errors. That is the worst kind of bug, so
packs are resolved against this at load time and refuse to start if a path is
wrong.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import date
from enum import IntEnum
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from qualify.schema.use_case_record import (
    COE_OWNED_ROOTS,
    DATA_MODEL_ROOT,
    UseCaseRecord,
    top_level_field,
)

#: Data-model root owned by the compiler rather than the record.
#:
#: Holds presentation state that has no business being persisted: the stage
#: caption, formatted copies of derived numbers, and so on. Keeping it in a
#: sibling tree to `/uc` means the record dump is never polluted with display
#: strings, and the ownership guard never has to reason about them.
UI_ROOT = "ui"


class PathError(ValueError):
    """Raised when a declared path does not exist on the record."""


@dataclass(frozen=True)
class ResolvedPath:
    """What a `/uc/...` path points at."""

    path: str
    #: The underlying scalar type, with Optional and list stripped off.
    scalar_type: type
    #: True when the field is a list, e.g. `technical.data_sources`.
    is_list: bool
    #: Top-level record field, e.g. `"business"`. Drives the ownership check.
    owner_root: str

    @property
    def is_coe_owned(self) -> bool:
        return self.owner_root in COE_OWNED_ROOTS

    @property
    def is_numeric(self) -> bool:
        return self.scalar_type in (int, float) and not issubclass(
            self.scalar_type, bool
        )

    @property
    def is_bool(self) -> bool:
        return self.scalar_type is bool

    @property
    def is_enum(self) -> bool:
        return isinstance(self.scalar_type, type) and issubclass(
            self.scalar_type, IntEnum
        )

    @property
    def dotted(self) -> str:
        """The provenance key form, e.g. `business.problem_description`."""
        return ".".join(_segments(self.path))


def _segments(path: str) -> list[str]:
    """Splits a path and drops the `/uc` root, if present."""
    parts = [s for s in path.replace(".", "/").split("/") if s]
    if parts and parts[0] == DATA_MODEL_ROOT:
        parts = parts[1:]
    return parts


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    """Strips `Optional`, `Annotated` and `list`, reporting whether it saw a list.

    Pydantic annotations arrive as things like
    `Annotated[int | None, Field(ge=0)]`, and every branch of that has to be
    peeled before the underlying type is comparable.
    """
    is_list = False

    # Annotated[X, ...] -> X
    while get_origin(annotation) is not None:
        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in (Union, types.UnionType):
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) != 1:
                return annotation, is_list
            annotation = non_none[0]
            continue

        if origin in (list, set, tuple, frozenset):
            is_list = True
            annotation = args[0]
            continue

        # Annotated and anything else exposing __metadata__.
        if hasattr(annotation, "__metadata__"):
            annotation = args[0]
            continue

        break

    return annotation, is_list


class _Missing:
    """Sentinel. `None` is a legitimate annotation, so it cannot mean absent."""


_MISSING = _Missing()


def _field_annotation(model: type[BaseModel], name: str) -> Any:
    """The annotation for `name`, whether it is declared or computed.

    `UseCaseRecord.derived` is a `@computed_field`, so it never appears in
    `model_fields`. It does appear in `model_dump()` and therefore in the A2UI
    data model, which means a pack can legitimately bind a read-only Text to
    `/uc/derived/total_annual_team_hours_saved`. Looking in only one of the
    two registries made that path unresolvable.
    """
    field = model.model_fields.get(name)
    if field is not None:
        return field.annotation

    computed = model.model_computed_fields.get(name)
    if computed is not None:
        return computed.return_type

    return _MISSING


def _field_names(model: type[BaseModel]) -> list[str]:
    return [*model.model_fields, *model.model_computed_fields]


def resolve_record_path(path: str) -> ResolvedPath:
    """Walks `path` down the `UseCaseRecord` model tree.

    Raises `PathError` with the closest thing to a helpful message we can
    produce — listing the valid names at the level that failed, because the
    usual cause is a near-miss spelling.
    """
    segments = _segments(path)
    if not segments:
        raise PathError(f"{path!r} names no field.")

    owner_root = segments[0]
    model: Any = UseCaseRecord
    scalar: Any = None
    is_list = False

    for depth, segment in enumerate(segments):
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            walked = "/".join(segments[:depth])
            raise PathError(
                f"{path!r} goes too deep: /uc/{walked} is a leaf, "
                f"not a container."
            )

        annotation = _field_annotation(model, segment)
        if annotation is _MISSING:
            walked = "/".join(segments[:depth]) or "(root)"
            raise PathError(
                f"{path!r} has no field {segment!r} under {walked}. "
                f"Valid names here: {sorted(_field_names(model))}"
            )

        scalar, saw_list = _unwrap(annotation)
        is_list = is_list or saw_list
        model = scalar

    if isinstance(scalar, type) and issubclass(scalar, BaseModel):
        raise PathError(
            f"{path!r} stops on a container, not a value. "
            f"Bind one of its fields instead: {sorted(scalar.model_fields)}"
        )

    return ResolvedPath(
        path=path,
        scalar_type=scalar if isinstance(scalar, type) else object,
        is_list=is_list,
        owner_root=owner_root,
    )


def is_ui_path(path: str) -> bool:
    """True for compiler-owned display paths under `/ui`."""
    parts = [s for s in path.split("/") if s]
    return bool(parts) and parts[0] == UI_ROOT


def assert_pack_path(path: str) -> ResolvedPath | None:
    """Validates a path declared in a pack.

    Returns the resolution for `/uc` paths and `None` for `/ui` paths, which
    are compiler-owned and have no record counterpart to check against.
    """
    if not path.startswith("/"):
        raise PathError(
            f"{path!r} must be an absolute JSON Pointer starting with '/'."
        )
    if is_ui_path(path):
        return None
    if top_level_field(path) == "" or not path.startswith(f"/{DATA_MODEL_ROOT}/"):
        raise PathError(
            f"{path!r} must sit under '/{DATA_MODEL_ROOT}/' (the record) "
            f"or '/{UI_ROOT}/' (compiler-owned display state)."
        )
    return resolve_record_path(path)


#: Types a `TextField` can sensibly edit.
TEXT_EDITABLE = (str, int, float, date)
