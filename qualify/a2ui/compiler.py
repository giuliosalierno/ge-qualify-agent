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

from qualify.a2ui.catalog import A2UI_VERSION, SCALAR_SINGLE_SELECT, catalog_id
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
_SKIP_ID = "skip-button"
_SKIP_LABEL_ID = "skip-label"
_STAGE_CAPTION_ID = "stage-caption"
_STAGE_TITLE_ID = "stage-title"
_STAGE_RULE_ID = "stage-rule"

#: Action fired by the Continue button. Routed in `a2ui/actions.py`.
COMMIT_STAGE = "commit_stage"
SKIP_STAGE = "skip_stage"

#: Components with no label slot of their own, which therefore need a
#: preceding `Text` caption.
#:
#: `MaterialSelect` is excluded because it does take a `label`. The other
#: three single-selects are bare controls: the catalog gives them `options`
#: and `value` and nothing to name them with.
_NEEDS_CAPTION = frozenset(
    {"Text", "MaterialText"} | (SCALAR_SINGLE_SELECT - {"MaterialSelect"})
)


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
    from qualify.a2ui.systems_extractor import render_systems_table  # noqa: PLC0415

    stage = pack.stages[active_stage]
    state: dict[str, Any] = {
        "stage": {
            "index": active_stage,
            "count": len(pack.stages),
            "id": stage.id,
            "caption": stage_caption(pack, active_stage),
        },
        "summary": summary_strings(record),
    }
    if pack.pack == "tech":
        state["systems"] = {"table": render_systems_table(record)}
    return state


def summary_strings(record: UseCaseRecord) -> dict[str, str]:
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

    if field.component in SCALAR_SINGLE_SELECT:
        # Verified in the L12 probe: each of these writes a plain JSON string
        # into the bound path. That is what makes enums collectable.
        options = [
            {"label": o.label, "value": o.value} for o in pack.options_for(field)
        ]
        node = {
            "id": cid,
            "component": field.component,
            "options": options,
            "value": {"path": field.path},
        }
        # Only MaterialSelect has a label slot. The other three are bare
        # controls, so their caption is emitted separately by the caller —
        # the same arrangement the L12 probe used in GE.
        if field.component == "MaterialSelect":
            node["label"] = _label_with_help(field)
            node["placeholder"] = "Choose one"
        return [node]

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


def build_stage_components(
    pack: Pack,
    stage_idx: int,
    committed_stages: set[int] | None = None,
    skipped_stages: set[int] | None = None,
) -> list[dict[str, Any]]:
    """The component list for one stage: caption, fields, Continue button.

    Returns a flat list. A2UI components are referenced by id rather than
    nested, so the root Column names its children and every child appears as a
    sibling entry.
    """
    stage: Stage = pack.stages[stage_idx]
    children: list[str] = [_STAGE_TITLE_ID]
    header: list[dict[str, Any]] = [
        {
            "id": _STAGE_TITLE_ID,
            "component": "Text",
            "text": pack.title,
            "variant": "h3",
        },
    ]

    committed_set = committed_stages or set()
    skipped_set = skipped_stages or set()
    has_prev_summaries = False

    for prev_idx, prev_stage in enumerate(pack.stages):
        if prev_idx == stage_idx:
            continue
        if prev_idx in committed_set or prev_idx in skipped_set:
            has_prev_summaries = True
            is_skipped = prev_idx in skipped_set
            title_id = f"prev-title-{prev_stage.id}"
            lbl_id = f"prev-revise-lbl-{prev_stage.id}"
            btn_id = f"prev-revise-btn-{prev_stage.id}"
            banner_text = (
                f"⚠ Stage {prev_idx + 1}: {prev_stage.label} — Skipped (Needs follow-up)"
                if is_skipped
                else f"✓ Stage {prev_idx + 1}: {prev_stage.label} — Confirmed"
            )
            btn_label = "Reopen & Fill" if is_skipped else "Revise"
            children.extend([title_id, btn_id])
            header.extend(
                [
                    {
                        "id": title_id,
                        "component": "Text",
                        "text": banner_text,
                        "variant": "caption",
                    },
                    {
                        "id": lbl_id,
                        "component": "Text",
                        "text": btn_label,
                    },
                    {
                        "id": btn_id,
                        "component": "Button",
                        "child": lbl_id,
                        "variant": "default",
                        "action": {
                            "event": {
                                "name": "revise_stage",
                                "context": {
                                    "prompt": f"Revise — {prev_stage.label}",
                                    "stage": prev_stage.id,
                                },
                            }
                        },
                    },
                ]
            )

    if has_prev_summaries:
        prev_rule_id = "prev-stages-rule"
        children.append(prev_rule_id)
        header.append({"id": prev_rule_id, "component": "Divider"})

    children.extend([_STAGE_CAPTION_ID, _STAGE_RULE_ID])
    header.extend(
        [
            {
                "id": _STAGE_CAPTION_ID,
                "component": "Text",
                "text": {"path": f"/{UI_ROOT}/stage/caption"},
                "variant": "caption",
            },
            {"id": _STAGE_RULE_ID, "component": "Divider"},
        ]
    )

    nodes: list[dict[str, Any]] = []

    for field in stage.fields:
        built = _field_component(pack, field)
        # Components with no label slot of their own get a caption above them.
        # Without it a radio group renders as six unexplained choices, and the
        # user has no idea what question they are answering.
        if field.component in _NEEDS_CAPTION and field.label:
            label_node = {
                "id": _label_id(field.path),
                "component": "Text",
                "text": _label_with_help(field),
                "variant": "caption",
            }
            nodes.append(label_node)
            children.append(label_node["id"])
        for node in built:
            nodes.append(node)
            children.append(node["id"])

    children.append(_CONTINUE_ID)
    if stage_idx > 0:
        children.append(_SKIP_ID)

    root = {"id": ROOT_ID, "component": "Column", "children": children}

    return [root, *header, *nodes, *build_continue_button(pack, stage_idx)]


def build_continue_button(pack: Pack, stage_idx: int) -> list[dict[str, Any]]:
    """The Continue button (and optional Skip for now button for Stages 2-4)."""
    stage = pack.stages[stage_idx]
    is_last = stage_idx == len(pack.stages) - 1
    label = "Submit" if is_last else "Continue"

    buttons: list[dict[str, Any]] = [
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

    if stage_idx > 0:
        buttons.extend(
            [
                {
                    "id": _SKIP_LABEL_ID,
                    "component": "Text",
                    "text": "Skip for now",
                },
                {
                    "id": _SKIP_ID,
                    "component": "Button",
                    "child": _SKIP_LABEL_ID,
                    "variant": "default",
                    "action": {
                        "event": {
                            "name": SKIP_STAGE,
                            "context": {
                                "prompt": f"Skip — {stage.label}",
                                "pack": pack.pack,
                                "stage": stage.id,
                                "data": {"path": f"/{DATA_MODEL_ROOT}"},
                            },
                        }
                    },
                },
            ]
        )

    return buttons


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def build_create_surface(surface_id: str = SURFACE_ID) -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "createSurface": {
            "surfaceId": surface_id,
            "catalogId": catalog_id(),
        },
    }


def build_update_components(
    components: list[dict[str, Any]], surface_id: str = SURFACE_ID
) -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "updateComponents": {
            "surfaceId": surface_id,
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
    pack: Pack,
    record: UseCaseRecord,
    active_stage: int,
    surface_id: str = SURFACE_ID,
    committed_stages: set[int] | None = None,
    skipped_stages: set[int] | None = None,
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
        build_create_surface(surface_id),
        build_update_components(
            build_stage_components(
                pack,
                active_stage,
                committed_stages=committed_stages,
                skipped_stages=skipped_stages,
            ),
            surface_id,
        ),
        build_patch("/", build_data_model(record, pack, active_stage), surface_id),
    ]


def build_completion_components(
    pack: Pack, record: UseCaseRecord, skipped_stages: set[int] | None = None
) -> list[dict[str, Any]]:
    """Builds a read-only summary card with reopen buttons for each stage."""
    sums = summary_strings(record)
    title = record.meta.initiative_name or "Qualified Use Case"
    skipped_set = skipped_stages or set()

    children = [
        "summary-title",
        "summary-caption",
        "summary-rule-1",
        "summary-hours",
        "summary-basis",
        "summary-rule-2",
        "summary-reopen-caption",
    ]

    status_text = (
        f"Record ID: {record.meta.record_id} | Gate 1: Follow-Up Items Pending"
        if skipped_set
        else f"Record ID: {record.meta.record_id} | Gate 1: Ready for CoE Review"
    )

    nodes: list[dict[str, Any]] = [
        {
            "id": "summary-title",
            "component": "Text",
            "text": f"Qualification Summary — {title}",
            "variant": "h3",
        },
        {
            "id": "summary-caption",
            "component": "Text",
            "text": status_text,
            "variant": "caption",
        },
        {"id": "summary-rule-1", "component": "Divider"},
        {
            "id": "summary-hours",
            "component": "Text",
            "text": sums["hours_line"],
            "variant": "body",
        },
        {
            "id": "summary-basis",
            "component": "Text",
            "text": sums["hours_basis"],
            "variant": "caption",
        },
        {"id": "summary-rule-2", "component": "Divider"},
        {
            "id": "summary-reopen-caption",
            "component": "Text",
            "text": "Need to adjust or complete an answer? Click below to reopen any stage:",
            "variant": "caption",
        },
    ]

    for idx, stage in enumerate(pack.stages):
        btn_id = f"revise-btn-{stage.id}"
        lbl_id = f"revise-lbl-{stage.id}"
        is_skipped = idx in skipped_set
        btn_text = (
            f"⚠ Fill Skipped Stage {idx + 1}: {stage.label}"
            if is_skipped
            else f"Reopen Stage {idx + 1}: {stage.label}"
        )
        children.append(btn_id)
        nodes.append(
            {
                "id": lbl_id,
                "component": "Text",
                "text": btn_text,
            }
        )
        nodes.append(
            {
                "id": btn_id,
                "component": "Button",
                "child": lbl_id,
                "variant": "default",
                "action": {
                    "event": {
                        "name": "revise_stage",
                        "context": {
                            "prompt": f"Revise — {stage.label}",
                            "stage": stage.id,
                        },
                    }
                },
            }
        )

    root = {"id": ROOT_ID, "component": "Column", "children": children}
    return [root, *nodes]


def build_completion_surface(
    pack: Pack,
    record: UseCaseRecord,
    surface_id: str = SURFACE_ID,
    skipped_stages: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Emits the final summary surface when all stages have been confirmed or skipped."""
    last_idx = len(pack.stages) - 1
    return [
        build_create_surface(surface_id),
        build_update_components(
            build_completion_components(pack, record, skipped_stages=skipped_stages),
            surface_id,
        ),
        build_patch("/", build_data_model(record, pack, last_idx), surface_id),
    ]


def build_collapsed_stage_components(
    pack: Pack, record: UseCaseRecord, stage_idx: int, skipped: bool = False
) -> list[dict[str, Any]]:
    """Builds a compact 1-line summary banner for a completed or skipped stage card."""
    stage = pack.stages[stage_idx]
    title_id = f"collapsed-title-{stage.id}"
    btn_id = f"collapsed-revise-btn-{stage.id}"
    lbl_id = f"collapsed-revise-lbl-{stage.id}"

    banner_text = (
        f"⚠ Stage {stage_idx + 1}: {stage.label} — Skipped (Needs follow-up)"
        if skipped
        else f"✓ Stage {stage_idx + 1}: {stage.label} — Confirmed"
    )
    btn_label = "Reopen & Fill" if skipped else "Revise"

    nodes: list[dict[str, Any]] = [
        {
            "id": title_id,
            "component": "Text",
            "text": banner_text,
            "variant": "caption",
        },
        {
            "id": lbl_id,
            "component": "Text",
            "text": btn_label,
        },
        {
            "id": btn_id,
            "component": "Button",
            "child": lbl_id,
            "variant": "default",
            "action": {
                "event": {
                    "name": "revise_stage",
                    "context": {
                        "prompt": f"Revise — {stage.label}",
                        "stage": stage.id,
                    },
                }
            },
        },
    ]

    root = {"id": ROOT_ID, "component": "Column", "children": [title_id, btn_id]}
    return [root, *nodes]


def build_collapsed_stage_patch(
    pack: Pack,
    record: UseCaseRecord,
    stage_idx: int,
    surface_id: str,
    skipped: bool = False,
) -> dict[str, Any]:
    """Emits an updateComponents message replacing the completed stage's form with a compact banner."""
    return build_update_components(
        build_collapsed_stage_components(pack, record, stage_idx, skipped=skipped),
        surface_id=surface_id,
    )


