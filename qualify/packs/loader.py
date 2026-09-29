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
from enum import IntEnum
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

from qualify.a2ui.catalog import (
    PACK_ALLOWED_COMPONENTS,
    PACK_WRITABLE_COMPONENTS,
    SCALAR_SINGLE_SELECT,
)
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
    # The scalar single-selects. None of them take a `variant`; their shape
    # is the component choice itself.
    "MaterialSelect": frozenset(),
    "MaterialRadioButton": frozenset(),
    "MaterialButtonToggle": frozenset(),
    "MaterialChips": frozenset(),
    "MaterialText": frozenset(),
}


class PackError(ValueError):
    """Raised when a pack is internally inconsistent."""


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


def options_from_enum(enum_cls: type[IntEnum]) -> list[Option]:
    """Derives the option list for an enum field from the enum itself.

    Written this way so a pack cannot drift from the ladder. If someone adds a
    seventh capability level, the form grows a seventh option with no YAML
    edit and no chance of the two disagreeing.

    The `value` is the member number as a string, because that is the type the
    widget writes. Coercing `"6"` back to `CapabilityLevel.HIGH_CODE_AGENT`
    happens once, on commit. Using the member *name* instead would read more
    nicely in logs but would need a reverse lookup that silently fails on a
    rename.
    """
    return [
        Option(label=getattr(member, "label", member.name), value=str(member.value))
        for member in enum_cls
    ]


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
                f"Widen qualify.a2ui.catalog.GE_RENDER_VERIFIED only "
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
        self._check_writable()
        self._check_type_match(resolved)

    def _check_writable(self) -> None:
        """Refuses an input component whose binding has never been watched.

        Rendering and binding are separate facts. A component that paints
        correctly and silently drops the user's input is worse than one that
        fails outright, because the form looks like it worked and the data
        loss surfaces days later in the Sheet.
        """
        if self.readonly:
            return
        if self.component not in PACK_WRITABLE_COMPONENTS:
            raise PackError(
                f"{self.path}: {self.component} has never been observed "
                f"writing a value back in GE, so it cannot hold input. "
                f"Writable: {sorted(PACK_WRITABLE_COMPONENTS)}. "
                f"Add it to GE_BIND_VERIFIED only after watching it write."
            )

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
                    f"scalar single-select ({sorted(SCALAR_SINGLE_SELECT)}) "
                    f"or a TextField."
                )
            return

        if r.is_list:
            raise PackError(
                f"{self.path}: this field is a list, which only ChoicePicker "
                f"can edit. Got {self.component}."
            )

        if self.component in SCALAR_SINGLE_SELECT:
            # Verified in the L12 probe: all four write a plain JSON string.
            # That makes them the right home for enums and for closed
            # vocabularies held as `str`.
            if not (r.scalar_type is str or r.is_enum):
                raise PackError(
                    f"{self.path}: {self.component} writes a string, but this "
                    f"field is {r.scalar_type.__name__}. Single-selects suit "
                    f"enums and closed vocabularies, not free numbers."
                )
            return

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
                    f"{self.path}: {r.scalar_type.__name__} is an enum, so a "
                    f"free-text box would accept values outside it. Use one "
                    f"of {sorted(SCALAR_SINGLE_SELECT)}; the options are "
                    f"derived from the enum automatically."
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
        needs_options = (
            self.component == "ChoicePicker" or self.component in SCALAR_SINGLE_SELECT
        )

        if self.options_ref and not needs_options:
            raise PackError(
                f"{self.path}: options_ref applies to ChoicePicker and the "
                f"single-selects, not {self.component}."
            )

        if not needs_options:
            return

        if self.component == "ChoicePicker" and not self.options_ref:
            raise PackError(f"{self.path}: ChoicePicker needs an options_ref.")

        r = self.resolved
        if r is None:
            return

        if r.is_enum:
            # The enum is the vocabulary. Letting a pack override it invites
            # a form that offers levels the record cannot store, so refuse
            # rather than silently picking one source over the other.
            if self.options_ref:
                raise PackError(
                    f"{self.path}: {r.scalar_type.__name__} is an enum, so "
                    f"the options come from the enum itself. Remove "
                    f"options_ref: {self.options_ref!r}."
                )
            return

        if not self.options_ref:
            raise PackError(
                f"{self.path}: {self.component} needs an options_ref. The "
                f"field is a plain string, so the vocabulary has to come "
                f"from the pack."
            )

    @property
    def enum_type(self) -> type[IntEnum] | None:
        """The enum backing this field, if it has one."""
        r = self.resolved
        if r is not None and r.is_enum:
            return r.scalar_type  # type: ignore[return-value]
        return None


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
        """The option list for a field, whatever its source.

        One call site for the compiler. A pack-declared vocabulary and an
        enum-derived one look identical from the outside, which is what keeps
        the compiler free of type-dispatch.
        """
        if field.options_ref:
            return self.option_sets[field.options_ref]

        enum_cls = field.enum_type
        if enum_cls is not None:
            return options_from_enum(enum_cls)

        return []


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
