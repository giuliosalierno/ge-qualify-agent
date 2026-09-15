"""Loading and validating question packs.

A pack is the data that describes one interview: its stages, the questions the
agent asks out loud, and the fields the form shows. Frozen contract 4.2.

The rule that shapes this file: **adding a field must never require touching
the compiler.** Everything the compiler needs is declared here, so a new
question is a YAML edit and a test run.

The second rule: **a bad pack fails at import, not mid-conversation.** Every
check below runs at load time. A path that does not exist on the record, a
component GE has never rendered, a `ChoicePicker` bound to a string — all of
them raise before the agent serves its first turn. The alternative is
discovering it when a user is halfway through an interview.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from qualify.a2ui.catalog import PACK_ALLOWED_COMPONENTS
from qualify.schema.paths import PathError, ResolvedPath, assert_pack_path

PACK_DIR = Path(__file__).parent

#: Components a pack may name, and the `variant` values each accepts.
#:
#: Taken from the catalog's own enums. Duplicated here so a bad variant is
#: caught at load time with a readable message rather than deep inside a
#: jsonschema failure.
COMPONENT_VARIANTS: dict[str, frozenset[str]] = {
    "TextField": frozenset({"shortText", "longText", "number", "obscured"}),
    "ChoicePicker": frozenset({"multipleSelection", "mutuallyExclusive"}),
    "CheckBox": frozenset(),
    "DateTimeInput": frozenset(),
    "Text": frozenset({"h1", "h2", "h3", "h4", "h5", "caption", "body"}),
}


class PackError(ValueError):
    """Raised when a pack is internally inconsistent."""


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class FieldSpec(BaseModel):
    """One input on the form."""

    model_config = ConfigDict(extra="forbid")

    path: str
    label: str
    component: str
    variant: str | None = None
    #: Required to leave the stage. Enforced server-side in `commit_stage`,
    #: not by the renderer — the base `Button` has no `disabled` prop, so
    #: there is no client-side gate to hang this on (see limitation L11).
    required: bool = False
    #: Shown under the input. Keep it short; it competes with the chat.
    help: str | None = None
    #: Names an entry in the pack's `option_sets`.
    options_ref: str | None = None
    #: Display only. Never written back, never committed.
    readonly: bool = False

    #: Filled in by the loader. Not part of the YAML.
    resolved: ResolvedPath | None = Field(default=None, exclude=True)

    @field_validator("component")
    @classmethod
    def _known_component(cls, v: str) -> str:
        if v not in PACK_ALLOWED_COMPONENTS:
            raise ValueError(
                f"{v!r} is not an allowed component. "
                f"Allowed: {sorted(PACK_ALLOWED_COMPONENTS)}. "
                f"Widen qualify.a2ui.catalog.GE_VERIFIED_COMPONENTS only "
                f"after watching the component render in GE."
            )
        return v

    @model_validator(mode="after")
    def _check(self) -> FieldSpec:
        self._check_variant()
        self._check_path()
        self._check_options()
        return self

    # -- individual checks ------------------------------------------------

    def _check_variant(self) -> None:
        allowed = COMPONENT_VARIANTS.get(self.component, frozenset())
        if self.variant is None:
            return
        if not allowed:
            raise PackError(
                f"{self.path}: {self.component} takes no variant, "
                f"got {self.variant!r}."
            )
        if self.variant not in allowed:
            raise PackError(
                f"{self.path}: {self.component} variant {self.variant!r} "
                f"is not one of {sorted(allowed)}."
            )

    def _check_path(self) -> None:
        resolved = assert_pack_path(self.path)

        if resolved is None:  # a /ui display path
            if not self.readonly:
                raise PackError(
                    f"{self.path}: /ui paths are compiler-owned display "
                    f"state and must be marked readonly."
                )
            return

        if resolved.is_coe_owned and not self.readonly:
            raise PackError(
                f"{self.path}: the '{resolved.owner_root}' tree is CoE-owned. "
                f"The Discovery Agent must not collect it. Put the user's "
                f"answer under /uc/proposed/ instead, or mark it readonly."
            )

        object.__setattr__(self, "resolved", resolved)
        self._check_type_match(resolved)

    def _check_type_match(self, r: ResolvedPath) -> None:
        """Rejects a component bound to a type it cannot edit.

        This is the check that earns the module. A `ChoicePicker` writes a
        string array; bound to `security.data_classification` (a `str`) it
        would round-trip `["confidential"]` into a field typed `str`, and
        pydantic would reject the commit long after the user filled the form.
        """
        if self.component == "ChoicePicker":
            if not r.is_list:
                raise PackError(
                    f"{self.path}: ChoicePicker writes a list of strings but "
                    f"this field is a scalar {r.scalar_type.__name__}. Use a "
                    f"TextField, or make the record field a list."
                )
            return

        if r.is_list:
            raise PackError(
                f"{self.path}: this field is a list, which only ChoicePicker "
                f"can edit. Got {self.component}."
            )

        if self.component == "CheckBox":
            if not r.is_bool:
                raise PackError(
                    f"{self.path}: CheckBox writes a boolean but this field "
                    f"is {r.scalar_type.__name__}."
                )
            return

        if self.component == "DateTimeInput":
            if r.scalar_type is not date:
                raise PackError(
                    f"{self.path}: DateTimeInput writes an ISO date string "
                    f"but this field is {r.scalar_type.__name__}."
                )
            return

        if self.component == "TextField":
            if r.is_bool:
                raise PackError(
                    f"{self.path}: use CheckBox for a boolean, not TextField."
                )
            if r.is_enum:
                raise PackError(
                    f"{self.path}: {r.scalar_type.__name__} is an enum. The "
                    f"verified GE component set has no scalar single-select "
                    f"(ChoicePicker binds to a list), so enums cannot be "
                    f"collected on the form yet. See limitation L12."
                )
            wants_number = self.variant == "number"
            if wants_number and not r.is_numeric:
                raise PackError(
                    f"{self.path}: variant 'number' but the field is "
                    f"{r.scalar_type.__name__}."
                )
            if r.is_numeric and not wants_number:
                raise PackError(
                    f"{self.path}: the field is numeric, so the TextField "
                    f"needs variant: number."
                )

    def _check_options(self) -> None:
        if self.component == "ChoicePicker" and not self.options_ref:
            raise PackError(f"{self.path}: ChoicePicker needs an options_ref.")
        if self.options_ref and self.component != "ChoicePicker":
            raise PackError(
                f"{self.path}: options_ref only applies to ChoicePicker, "
                f"got {self.component}."
            )


class Stage(BaseModel):
    """One step of the interview."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    #: What the agent says out loud. The form and the chat ask the same
    #: things; the chat is the conversation, the form is the receipt.
    chat_questions: list[str] = Field(default_factory=list)
    fields: list[FieldSpec]
    #: Human-readable, for the SKILL prompt and for review. The machine gate
    #: is `required_paths` below, derived from the fields.
    exit_criteria: list[str] = Field(default_factory=list)

    @property
    def required_paths(self) -> list[str]:
        """Paths that must be user-confirmed before the stage can close."""
        return [f.path for f in self.fields if f.required and not f.readonly]

    @property
    def writable_paths(self) -> list[str]:
        return [f.path for f in self.fields if not f.readonly]


class Pack(BaseModel):
    """A complete interview definition."""

    model_config = ConfigDict(extra="forbid")

    pack: str
    version: str
    title: str
    root_path: str = "/uc"
    #: Named option lists, referenced by `FieldSpec.options_ref`. Inline
    #: rather than a separate file so a pack is one reviewable artefact.
    option_sets: dict[str, list[Option]] = Field(default_factory=dict)
    stages: list[Stage]

    @model_validator(mode="after")
    def _check(self) -> Pack:
        if not self.stages:
            raise PackError(f"pack {self.pack!r} has no stages.")

        seen_stages: set[str] = set()
        seen_paths: dict[str, str] = {}

        for stage in self.stages:
            if stage.id in seen_stages:
                raise PackError(f"duplicate stage id {stage.id!r}.")
            seen_stages.add(stage.id)

            if not stage.fields:
                raise PackError(f"stage {stage.id!r} has no fields.")

            for f in stage.fields:
                if f.path in seen_paths:
                    raise PackError(
                        f"{f.path} appears in both stage "
                        f"{seen_paths[f.path]!r} and {stage.id!r}. A field "
                        f"belongs to exactly one stage, otherwise two "
                        f"Continue buttons claim the same value."
                    )
                seen_paths[f.path] = stage.id

                if f.options_ref and f.options_ref not in self.option_sets:
                    raise PackError(
                        f"{f.path}: options_ref {f.options_ref!r} is not in "
                        f"option_sets {sorted(self.option_sets)}."
                    )

        return self

    def stage_by_id(self, stage_id: str) -> Stage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise KeyError(f"pack {self.pack!r} has no stage {stage_id!r}.")

    def options_for(self, field: FieldSpec) -> list[Option]:
        if not field.options_ref:
            return []
        return self.option_sets[field.options_ref]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_pack_file(path: Path) -> Pack:
    """Parses and validates one pack file.

    Every failure comes back as a `PackError` naming the file. Pydantic wraps
    anything raised inside a validator in its own `ValidationError`, so
    without the second `except` the careful messages above never reach the
    caller — they arrive as a pydantic traceback pointing at a list index.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackError(f"{path.name}: not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise PackError(f"{path.name}: expected a mapping at the top level.")

    try:
        return Pack.model_validate(raw)
    except (PackError, PathError) as exc:
        raise PackError(f"{path.name}: {exc}") from exc
    except ValidationError as exc:
        raise PackError(f"{path.name}: {_readable(exc)}") from exc


def _readable(exc: ValidationError) -> str:
    """Flattens a pydantic error into one line per problem.

    Keeps the original message, which for our own validators is the text
    written above, and prefixes the field location so the reader knows which
    of forty fields is at fault.
    """
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{loc}: {err['msg']}")
    return " | ".join(lines)


@lru_cache(maxsize=None)
def load_pack(name: str) -> Pack:
    """Loads `packs/<name>.yaml`."""
    path = PACK_DIR / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in PACK_DIR.glob("*.yaml"))
        raise PackError(f"no pack {name!r}. Available: {available}")
    return load_pack_file(path)


def load_all_packs() -> dict[str, Pack]:
    """Loads every pack in the directory.

    Called at startup so a broken pack takes the process down immediately,
    which is the whole point.
    """
    return {p.stem: load_pack(p.stem) for p in sorted(PACK_DIR.glob("*.yaml"))}
