"""Probe for limitation L12 — is there a scalar single-select that works in GE?

The problem
-----------
`technical.capability_level` is a six-value enum. The only single-select this
project has verified in Gemini Enterprise is `ChoicePicker`, and `ChoicePicker`
writes a **string array** even in `mutuallyExclusive` mode. Binding it to a
scalar field would round-trip `["high_code_agent"]` into a field typed as an
enum, and pydantic would reject the commit long after the user filled the form.
So the pack loader currently refuses enum fields outright.

The catalog offers four components whose `value` is a scalar `DynamicString`:

| Component | Shape |
| :--- | :--- |
| `MaterialSelect` | dropdown |
| `MaterialRadioButton` | radio group |
| `MaterialButtonToggle` | segmented buttons |
| `MaterialChips` | selectable chips |

All four are Material, and no Material component has ever been seen rendering
in GE. That is the whole question.

What this probe answers
-----------------------
1. Does any Material component render in GE at all?
2. Do these four bind a scalar, or do they surprise us like `ChoicePicker` did?
3. Does mixing base and Material components in one surface work?

Design notes
------------
Every Material widget is preceded by a **base** `Text` label, because base
`Text` is known to render. If a label appears with nothing under it, that
component specifically failed. If nothing appears at all, the surface failed
and the result says nothing about any individual component. Without the
labels those two outcomes look identical.

The widgets can also be rendered one at a time (`sel select`, `sel radio`, …)
so a failure of the combined surface can be bisected rather than guessed at.
"""

from typing import Any

WIRE_VERSION = "v0.9"

GE_COMPOSITE_CATALOG_ID = (
    "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/"
    "gemini_enterprise_composite_catalog.json"
)

#: Deliberately not the echo probe's surface. Run this in a fresh GE
#: conversation so exactly one surface exists and multiple concurrent
#: surfaces — themselves untested — cannot confound the result.
SURFACE_ID = "l12-select-probe"

ACTION_SUBMIT = "spike_select_submit"

#: The real capability ladder, so this doubles as a dry run of the field we
#: actually want to collect rather than a toy example.
LADDER = [
    {"label": "1 — Default assistant", "value": "default_assistant"},
    {"label": "2 — Assistant with custom skill", "value": "custom_skill"},
    {"label": "3 — Workflow Builder chat agent", "value": "wb_chat_agent"},
    {"label": "4 — Workflow Builder workflow agent", "value": "wb_workflow_agent"},
    {"label": "5 — Workflow agent with custom MCP", "value": "wb_custom_mcp"},
    {"label": "6 — Custom high-code agent", "value": "high_code_agent"},
]

#: Short list for the widgets that lay options out horizontally. Six segmented
#: buttons in a chat panel would wrap into noise and the test would measure
#: layout rather than binding.
SHORT = [
    {"label": "Low-code", "value": "low_code"},
    {"label": "Pro-code", "value": "pro_code"},
]


def _label(node_id: str, text: str) -> dict[str, Any]:
    """A base `Text`, which Phase 0 confirmed renders.

    Its presence with an empty slot beneath it is the signal that one specific
    Material component failed.
    """
    return {"id": node_id, "component": "Text", "text": text, "variant": "caption"}


#: Each entry: the id prefix, the label, and the widget itself.
#: Keyed so a single widget can be rendered in isolation.
WIDGETS: dict[str, dict[str, Any]] = {
    "select": {
        "label": "MaterialSelect — dropdown, binds /sel/dropdown",
        "node": {
            "id": "w-select",
            "component": "MaterialSelect",
            "label": "Capability level",
            "placeholder": "Choose one",
            "options": LADDER,
            "value": {"path": "/sel/dropdown"},
        },
    },
    "radio": {
        "label": "MaterialRadioButton — radio group, binds /sel/radio",
        "node": {
            "id": "w-radio",
            "component": "MaterialRadioButton",
            "options": LADDER,
            "value": {"path": "/sel/radio"},
        },
    },
    "toggle": {
        "label": "MaterialButtonToggle — segmented, binds /sel/toggle",
        "node": {
            "id": "w-toggle",
            "component": "MaterialButtonToggle",
            "options": SHORT,
            "value": {"path": "/sel/toggle"},
        },
    },
    "chips": {
        "label": "MaterialChips — chips, binds /sel/chips",
        "node": {
            "id": "w-chips",
            "component": "MaterialChips",
            "options": SHORT,
            "value": {"path": "/sel/chips"},
        },
    },
    # A control. MaterialText is the simplest Material component there is.
    # If even this does not render, the answer is "GE renders no Material at
    # all" and the other four results carry no information.
    "text": {
        "label": "MaterialText — control, static text",
        "node": {
            "id": "w-text",
            "component": "MaterialText",
            "text": "If you can read this line, Material components render.",
            "usageHint": "body",
        },
    },
}

#: Order matters: the control goes first so its result is visible even if a
#: later component breaks the render.
ORDER = ["text", "select", "radio", "toggle", "chips"]

#: Every path the widgets write to, echoed back on submit.
BOUND_PATHS = ["dropdown", "radio", "toggle", "chips"]


def build_select_probe(only: str | None = None) -> list[dict[str, Any]]:
    """The A2UI message sequence for the L12 probe.

    Args:
        only: render a single widget by key, e.g. `"select"`. `None` renders
            all of them. Bisecting matters here — a combined surface that
            fails tells you nothing about which component caused it.
    """
    keys = [only] if only else ORDER
    keys = [k for k in keys if k in WIDGETS]

    components: list[dict[str, Any]] = []
    children: list[str] = ["title", "hint", "rule"]

    components.append(
        {
            "id": "title",
            "component": "Text",
            "text": "L12 — scalar single-select probe",
            "variant": "h3",
        }
    )
    components.append(
        {
            "id": "hint",
            "component": "Text",
            "text": (
                "Each caption below names a component. If a caption appears"
                " with nothing under it, that component did not render."
                " Pick a value in whichever widgets work, then press Submit."
            ),
        }
    )
    components.append({"id": "rule", "component": "Divider"})

    for key in keys:
        widget = WIDGETS[key]
        label_id = f"l-{key}"
        components.append(_label(label_id, widget["label"]))
        components.append(widget["node"])
        children.extend([label_id, widget["node"]["id"]])

    # Base Button and base Text: both verified in Phase 0, so a failure to
    # submit cannot be blamed on the button.
    components.append({"id": "submit-text", "component": "Text", "text": "Submit"})
    components.append(
        {
            "id": "submit",
            "component": "Button",
            "child": "submit-text",
            "variant": "primary",
            "action": {
                "event": {
                    "name": ACTION_SUBMIT,
                    "context": {
                        "prompt": "Submit the select probe",
                        # The whole subtree, so we see the exact JSON type each
                        # widget wrote — scalar string, array, or nothing.
                        "all": {"path": "/sel"},
                        # Also bound individually. If the subtree comes back
                        # empty we still learn whether any single path resolved.
                        **{p: {"path": f"/sel/{p}"} for p in BOUND_PATHS},
                    },
                }
            },
        }
    )
    children.append("submit")

    root = {
        "id": "root",
        "component": "Column",
        "align": "stretch",
        "children": children,
    }

    return [
        {
            "version": WIRE_VERSION,
            "createSurface": {
                "surfaceId": SURFACE_ID,
                "catalogId": GE_COMPOSITE_CATALOG_ID,
            },
        },
        {
            "version": WIRE_VERSION,
            "updateComponents": {
                "surfaceId": SURFACE_ID,
                "components": [root, *components],
            },
        },
        {
            "version": WIRE_VERSION,
            "updateDataModel": {
                "surfaceId": SURFACE_ID,
                "path": "/",
                # Seeded empty rather than pre-selected. A pre-selected value
                # would make "the widget wrote a scalar" indistinguishable from
                # "our seed survived".
                "value": {"sel": {p: "" for p in BOUND_PATHS}},
            },
        },
    ]
