"""Turning a pack plus a record into A2UI messages. Frozen contract 4.3.

Pure data in, pure data out. No I/O, no model calls, no globals. That is what
makes every function here snapshot-testable, and it is why the golden tests
can assert on exact JSON.

Three decisions worth knowing before reading the code.

**No Stepper.** The plan called for one. The GE composite catalog has 52
components and `Stepper` is not among them. Tabs would let the user jump to
stage 4 without answering stage 1, which defeats the gating, so the progress
indicator is a plain `Text` caption: "Stage 2 of 4 — Effort and value". Less
pretty, no way to break the flow.

**Base components only.** The catalog offers both a base set and a Material
set. Phase 0 watched `Column`, `Text`, `TextField`, `Button` and `Divider`
render in Gemini Enterprise. The Material components are untested there. In a
catalog this large, "it validates" and "it renders" are different claims.

**The Continue button carries the whole data model.** `sendDataModel: true`
is a no-op in GE — `message.metadata` came back null on every turn of the
Phase 0 probe, including with unsaved edits in the form. The only way to get
the user's typing back is to ask for it in an action context, and Phase 0
confirmed a context entry of `{"path": "/uc"}` resolves to the entire subtree.
So that is what every Continue button sends. Decision D14.
"""

from __future__ import annotations

from typing import Any

from qualify.a2ui.catalog import A2UI_VERSION, catalog_id
from qualify.packs.loader import FieldSpec, Pack, Stage
from qualify.schema.paths import UI_ROOT
from qualify.schema.use_case_record import (
    DATA_MODEL_ROOT,
    UseCaseRecord,
    WORK_WEEKS_PER_YEAR,
)

#: One surface per conversation. Phase 0 established that surfaces are scoped
#: to the conversation, survive a browser reload and stay patchable, so the id
#: can be a constant rather than something derived per turn.
SURFACE_ID = "qualify"

ROOT_ID = "root"
_CONTINUE_ID = "continue-button"
_CONTINUE_LABEL_ID = "continue-label"
_STAGE_CAPTION_ID = "stage-caption"
_STAGE_TITLE_ID = "stage-title"
_STAGE_RULE_ID = "stage-rule"

#: Action fired by the Continue button. Routed in `a2ui/actions.py`.
COMMIT_STAGE = "commit_stage"


# ---------------------------------------------------------------------------
# Component ids
# ---------------------------------------------------------------------------


def component_id(path: str) -> str:
    """A stable component id for a data-model path.

    Deterministic because ids are the handle for later `updateComponents`
    patches. Deriving them from the path means the compiler never has to
    remember what it called something on a previous turn.

        /uc/business/problem_description -> f-uc-business-problem-description
    """
    return "f-" + path.strip("/").replace("/", "-").replace("_", "-")


def _label_id(path: str) -> str:
    return component_id(path) + "-label"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def build_data_model(
    record: UseCaseRecord, pack: Pack, active_stage: int
) -> dict[str, Any]:
    """The full data model tree: the record under `/uc`, display state under `/ui`.

    Splitting them keeps the record clean. The user needs to see
    "About 1,000 hours a year across the team", but that sentence is
    presentation, not data, and storing it would mean a formatted string could
    drift from the number it claims to describe.
    """
    return {
        DATA_MODEL_ROOT: record.model_dump(mode="json"),
        UI_ROOT: build_ui_state(record, pack, active_stage),
    }


def build_ui_state(
    record: UseCaseRecord, pack: Pack, active_stage: int
) -> dict[str, Any]:
    """Compiler-owned display state."""
    stage = pack.stages[active_stage]
    return {
        "stage": {
            "index": active_stage,
            "count": len(pack.stages),
            "id": stage.id,
            "caption": stage_caption(pack, active_stage),
        },
        "summary": _summary_strings(record),
    }


def _summary_strings(record: UseCaseRecord) -> dict[str, str]:
    """Human sentences for the derived numbers.

    Returns a prompt rather than an empty string when the inputs are missing.
    A blank line looks like a rendering bug; "Fill in the three numbers above"
    tells the user what to do next.
    """
    d = record.derived
    total = d.total_annual_team_hours_saved
    per_user = d.annual_hours_saved_per_user

    if per_user is None:
        return {
            "hours_line": "Fill in the three numbers above to see the estimate.",
            "hours_basis": "",
        }

    if total is None:
        line = f"About {per_user:,.0f} hours a year per person."
        basis_tail = "Add the number of people to get a team total."
    else:
        line = (
            f"About {total:,.0f} hours a year across the team, "
            f"{per_user:,.0f} per person."
        )
        basis_tail = ""

    freq = record.sizing.task_frequency_weekly
    saved = record.sizing.target_minutes_saved_per_task
    basis = (
        f"{freq:g} times a week x {saved:g} minutes saved "
        f"x {WORK_WEEKS_PER_YEAR} working weeks"
    )
    if record.business.user_count is not None:
        basis += f" x {record.business.user_count} people"
    if basis_tail:
        basis = f"{basis}. {basis_tail}"

    return {"hours_line": line, "hours_basis": basis}


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def stage_caption(pack: Pack, stage_idx: int) -> str:
    stage = pack.stages[stage_idx]
    return f"Stage {stage_idx + 1} of {len(pack.stages)} — {stage.label}"


def _field_component(pack: Pack, field: FieldSpec) -> list[dict[str, Any]]:
    """One field, as the component(s) needed to render it.

    Most fields are a single component. `Text` fields need a preceding label,
    because unlike `TextField` the base `Text` component has nowhere to put
    one.
    """
    cid = component_id(field.path)

    if field.component == "Text":
        node: dict[str, Any] = {
            "id": cid,
            "component": "Text",
            "text": {"path": field.path},
        }
        if field.variant:
            node["variant"] = field.variant
        return [node]

    if field.component == "TextField":
        node = {
            "id": cid,
            "component": "TextField",
            "label": _label_with_help(field),
            "value": {"path": field.path},
        }
        if field.variant:
            node["variant"] = field.variant
        return [node]

    if field.component == "ChoicePicker":
        return [
            {
                "id": cid,
                "component": "ChoicePicker",
                "label": _label_with_help(field),
                "variant": field.variant or "mutuallyExclusive",
                "options": [
                    {"label": o.label, "value": o.value}
                    for o in pack.options_for(field)
                ],
                "value": {"path": field.path},
            }
        ]

    if field.component == "CheckBox":
        return [
            {
                "id": cid,
                "component": "CheckBox",
                "label": _label_with_help(field),
                "value": {"path": field.path},
            }
        ]

    if field.component == "DateTimeInput":
        return [
            {
                "id": cid,
                "component": "DateTimeInput",
                "label": _label_with_help(field),
                "enableDate": True,
                "value": {"path": field.path},
            }
        ]

    # Unreachable: the loader rejects unknown components at import time. Kept
    # so that widening PACK_ALLOWED_COMPONENTS without touching the compiler
    # fails loudly rather than rendering nothing.
    raise NotImplementedError(
        f"{field.component} is allowed in packs but the compiler cannot "
        f"build it yet."
    )


def _label_with_help(field: FieldSpec) -> str:
    """Folds required-ness and help text into the one label slot available.

    The base components expose a `label` and nothing else — no hint, no
    supporting text. Rather than emit a second `Text` component per field and
    double the tree, the extra information goes inline.
    """
    label = field.label
    if field.required:
        label += " *"
    if field.help:
        label += f" — {field.help}"
    return label


def build_stage_components(pack: Pack, stage_idx: int) -> list[dict[str, Any]]:
    """The component list for one stage: caption, fields, Continue button.

    Returns a flat list. A2UI components are referenced by id rather than
    nested, so the root Column names its children and every child appears as a
    sibling entry.
    """
    stage: Stage = pack.stages[stage_idx]
    children: list[str] = [_STAGE_TITLE_ID, _STAGE_CAPTION_ID, _STAGE_RULE_ID]
    nodes: list[dict[str, Any]] = []

    for field in stage.fields:
        built = _field_component(pack, field)
        # A readonly Text has no label of its own, so give it one.
        if field.component == "Text" and field.label:
            label_node = {
                "id": _label_id(field.path),
                "component": "Text",
                "text": field.label,
                "variant": "caption",
            }
            nodes.append(label_node)
            children.append(label_node["id"])
        for node in built:
            nodes.append(node)
            children.append(node["id"])

    children.append(_CONTINUE_ID)

    header = [
        {
            "id": _STAGE_TITLE_ID,
            "component": "Text",
            "text": pack.title,
            "variant": "h3",
        },
        {
            "id": _STAGE_CAPTION_ID,
            "component": "Text",
            "text": {"path": f"/{UI_ROOT}/stage/caption"},
            "variant": "caption",
        },
        {"id": _STAGE_RULE_ID, "component": "Divider"},
    ]

    root = {"id": ROOT_ID, "component": "Column", "children": children}

    return [root, *header, *nodes, *build_continue_button(pack, stage_idx)]


def build_continue_button(pack: Pack, stage_idx: int) -> list[dict[str, Any]]:
    """The Continue button, and the `Text` that is its child.

    The base `Button` requires a child component and has no `disabled` prop,
    so there is no client-side gate here. Required fields are enforced
    server-side when `commit_stage` arrives, which is the only enforcement
    that counts anyway — a disabled button stops an honest user, not a
    malformed payload.

    The context carries `{"path": "/uc"}`, which Phase 0 confirmed resolves to
    the whole record subtree. That is the workaround for `sendDataModel` being
    a no-op in GE (D14).
    """
    stage = pack.stages[stage_idx]
    is_last = stage_idx == len(pack.stages) - 1
    label = "Submit" if is_last else "Continue"

    return [
        {
            "id": _CONTINUE_LABEL_ID,
            "component": "Text",
            "text": label,
        },
        {
            "id": _CONTINUE_ID,
            "component": "Button",
            "child": _CONTINUE_LABEL_ID,
            "variant": "primary",
            "action": {
                "event": {
                    "name": COMMIT_STAGE,
                    "context": {
                        "prompt": f"{label} — {stage.label}",
                        "pack": pack.pack,
                        "stage": stage.id,
                        "data": {"path": f"/{DATA_MODEL_ROOT}"},
                    },
                }
            },
        },
    ]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def build_create_surface() -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "createSurface": {
            "surfaceId": SURFACE_ID,
            "catalogId": catalog_id(),
        },
    }


def build_update_components(components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "updateComponents": {
            "surfaceId": SURFACE_ID,
            "components": components,
        },
    }


def build_patch(path: str, value: Any, surface_id: str = SURFACE_ID) -> dict[str, Any]:
    """A single `updateDataModel` message.

    Phase 0 verified a bare patch works: no `createSurface`, no
    `updateComponents`, and the rendered field still moves. This is how the
    form fills itself in while the user talks.
    """
    return {
        "version": A2UI_VERSION,
        "updateDataModel": {
            "surfaceId": surface_id,
            "path": path,
            "value": value,
        },
    }


def build_surface(
    pack: Pack, record: UseCaseRecord, active_stage: int
) -> list[dict[str, Any]]:
    """The full message sequence for a stage.

    Order matters: the surface must exist before components reference it, and
    components must exist before the data model tries to fill them.
    """
    if not 0 <= active_stage < len(pack.stages):
        raise IndexError(
            f"stage {active_stage} is out of range for pack {pack.pack!r}, "
            f"which has {len(pack.stages)}."
        )

    return [
        build_create_surface(),
        build_update_components(build_stage_components(pack, active_stage)),
        build_patch("/", build_data_model(record, pack, active_stage)),
    ]
