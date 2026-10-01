"""Diagnostic surfaces for the side-panel canvas UX.

Triggered by typing ``probe canvas``. Nothing here is used by the interview;
it exists to measure what Gemini Enterprise actually renders before the brief
editor and portfolio chart get built on top of it.

The GE composite catalog declares ``Canvas``, ``Tabs``, ``VegaChart``,
``GcbpTable``, ``MaterialExpansionPanel``, ``MaterialSlider`` and
``MaterialBadge``, but the catalog describes the schema, not the renderer
(see ``catalog.GE_RENDER_VERIFIED``). None of them has been seen in GE yet.

Three surfaces go out separately so that one failing component cannot blank
the others:

1. ``probe-canvas-min``: a ``Canvas`` holding only ``Text``. Answers "does
   the side panel work at all".
2. ``probe-canvas-full``: a ``Canvas`` with ``Tabs`` over a chart, a table
   and an edit form. The Save button sends ``PROBE_CANVAS_ECHO`` with the
   slider and text-field values bound in, so the round trip shows whether
   the inputs write back and in what type.
3. ``probe-inline-chart``: the same chart inline, outside any canvas, so a
   blank chart in (2) can be told apart from a broken ``VegaChart``.
"""

from __future__ import annotations

from typing import Any

from qualify.a2ui.compiler import (
    build_create_surface,
    build_patch,
    build_update_components,
)

#: Event the probe's Save button raises. Handled in ``turn.py`` before normal
#: dispatch, so it never touches a session's record.
PROBE_CANVAS_ECHO = "probe_canvas_echo"

PROBE_TRIGGERS = ("probe canvas", "a2ui probe canvas")

MIN_SURFACE_ID = "probe-canvas-min"
FULL_SURFACE_ID = "probe-canvas-full"
INLINE_SURFACE_ID = "probe-inline-chart"

_DATA_ROOT = "/ui/probe"

#: Mirrors the seeded Cymbal samples, so the chart looks like the real one.
_SAMPLE_POINTS = [
    {"name": "KYC pre-check", "value": 5, "feasibility": 4, "hours": 12500, "quadrant": "Quick Win"},
    {"name": "Claims triage", "value": 5, "feasibility": 4.2, "hours": 28000, "quadrant": "Quick Win"},
    {"name": "Store policy", "value": 5, "feasibility": 1, "hours": 45000, "quadrant": "Blocked"},
    {"name": "test", "value": 2, "feasibility": 3, "hours": 500, "quadrant": "Deprioritized"},
]


def _quadrant_chart_spec() -> dict[str, Any]:
    """Value × feasibility scatter with the quick-win thresholds drawn in."""
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": "container",
        "layer": [
            {
                "data": {"values": [{"x": 4}]},
                "mark": {"type": "rule", "strokeDash": [4, 4], "color": "#9aa0a6"},
                "encoding": {"x": {"field": "x", "type": "quantitative"}},
            },
            {
                "data": {"values": [{"y": 3}]},
                "mark": {"type": "rule", "strokeDash": [4, 4], "color": "#9aa0a6"},
                "encoding": {"y": {"field": "y", "type": "quantitative"}},
            },
            {
                "data": {"values": _SAMPLE_POINTS},
                "mark": {"type": "circle", "opacity": 0.8},
                "encoding": {
                    "x": {
                        "field": "feasibility",
                        "type": "quantitative",
                        "title": "Feasibility",
                        "scale": {"domain": [0, 5.5]},
                    },
                    "y": {
                        "field": "value",
                        "type": "quantitative",
                        "title": "Business value",
                        "scale": {"domain": [0, 5.5]},
                    },
                    "size": {
                        "field": "hours",
                        "type": "quantitative",
                        "title": "Hours saved / yr",
                        "scale": {"range": [80, 1400]},
                    },
                    "color": {"field": "quadrant", "type": "nominal", "title": "Quadrant"},
                    "tooltip": [
                        {"field": "name", "type": "nominal"},
                        {"field": "hours", "type": "quantitative", "format": ","},
                    ],
                },
            },
        ],
    }


def _surface(
    surface_id: str,
    components: list[dict[str, Any]],
    data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    messages = [
        build_create_surface(surface_id),
        build_update_components(components, surface_id),
    ]
    if data is not None:
        messages.append(build_patch("/", data, surface_id))
    return messages


def _min_canvas() -> list[dict[str, Any]]:
    return _surface(
        MIN_SURFACE_ID,
        [
            {
                "id": "root",
                "component": "Canvas",
                "children": ["p1-title", "p1-body"],
                "autoOpen": False,
                "cardTitle": "Probe 1 — minimal canvas",
                "cardDescription": "Click to open. Contains text only.",
                "cardIcon": "science",
            },
            {"id": "p1-title", "component": "Text", "text": "Probe 1 opened", "variant": "h3"},
            {
                "id": "p1-body",
                "component": "Text",
                "text": "If you can read this in a side panel, the Canvas component works.",
                "variant": "body",
            },
        ],
    )


def _full_canvas() -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = [
        {
            "id": "root",
            "component": "Canvas",
            "children": ["p2-title", "p2-tabs"],
            "autoOpen": True,
            "cardTitle": "Probe 2 — brief editor",
            "cardDescription": "Should open by itself. Tabs: chart, table, edit.",
            "cardIcon": "edit_document",
        },
        {
            "id": "p2-title",
            "component": "Text",
            "text": "Business Value Brief — UC-2026-PROBE",
            "variant": "h3",
        },
        {
            "id": "p2-tabs",
            "component": "Tabs",
            "tabs": [
                {"title": "Edit", "child": "p2-edit"},
                {"title": "Portfolio chart", "child": "p2-chart"},
                {"title": "Table", "child": "p2-table"},
            ],
        },
        # --- Edit tab -------------------------------------------------------
        {"id": "p2-edit", "component": "Column", "children": ["p2-panel", "p2-save"]},
        {
            "id": "p2-panel",
            "component": "MaterialExpansionPanel",
            "title": "Business value",
            "description": "Expansion panel, starts expanded",
            "expanded": True,
            "children": ["p2-name", "p2-users-caption", "p2-users", "p2-badge"],
        },
        {
            "id": "p2-name",
            "component": "TextField",
            "label": "Initiative name",
            "value": {"path": f"{_DATA_ROOT}/name"},
        },
        {
            "id": "p2-users-caption",
            "component": "Text",
            "text": "Users reached (MaterialSlider, 0–1000)",
            "variant": "caption",
        },
        {
            "id": "p2-users",
            "component": "MaterialSlider",
            "min": 0,
            "max": 1000,
            "step": 10,
            "color": "primary",
            "ariaLabel": "Users reached",
            "value": {"path": f"{_DATA_ROOT}/users"},
        },
        {
            "id": "p2-badge",
            "component": "MaterialBadge",
            "text": "QW",
            "color": "accent",
            "children": ["p2-badge-label"],
        },
        {
            "id": "p2-badge-label",
            "component": "Text",
            "text": "Quadrant (MaterialBadge should show QW)",
            "variant": "body",
        },
        {"id": "p2-save-label", "component": "Text", "text": "Save"},
        {
            "id": "p2-save",
            "component": "Button",
            "child": "p2-save-label",
            "variant": "primary",
            "action": {
                "event": {
                    "name": PROBE_CANVAS_ECHO,
                    "context": {
                        "prompt": "Save — canvas probe",
                        "name": {"path": f"{_DATA_ROOT}/name"},
                        "users": {"path": f"{_DATA_ROOT}/users"},
                    },
                }
            },
        },
        # --- Chart tab ------------------------------------------------------
        # `spec` is a DynamicValue: string, number, array or binding, but not
        # an inline object. So the spec lives in the data model.
        {
            "id": "p2-chart",
            "component": "VegaChart",
            "spec": {"path": f"{_DATA_ROOT}/chart"},
            "height": 320,
        },
        # --- Table tab ------------------------------------------------------
        {
            "id": "p2-table",
            "component": "GcbpTable",
            "caption": "Ranked portfolio",
            "columns": [
                {"header": "Initiative", "field": "name", "sortable": False},
                {"header": "Value", "field": "value", "sortable": False},
                {"header": "Feasibility", "field": "feasibility", "sortable": False},
                {"header": "Hours / yr", "field": "hours", "sortable": False},
                {"header": "Quadrant", "field": "quadrant"},
            ],
            # The catalog types every cell as a string.
            "rows": [{k: str(v) for k, v in row.items()} for row in _SAMPLE_POINTS],
        },
    ]
    data = {
        "ui": {
            "probe": {
                "name": "Claims intake triage",
                "users": 120,
                "chart": _quadrant_chart_spec(),
            }
        }
    }
    return _surface(FULL_SURFACE_ID, components, data)


def _inline_chart() -> list[dict[str, Any]]:
    return _surface(
        INLINE_SURFACE_ID,
        [
            {"id": "root", "component": "Column", "children": ["p3-caption", "p3-chart"]},
            {
                "id": "p3-caption",
                "component": "Text",
                "text": "Probe 3 — the same VegaChart inline, outside any canvas",
                "variant": "caption",
            },
            {
                "id": "p3-chart",
                "component": "VegaChart",
                "spec": {"path": f"{_DATA_ROOT}/chart"},
                "height": 280,
            },
        ],
        {"ui": {"probe": {"chart": _quadrant_chart_spec()}}},
    )


def build_canvas_probe() -> list[dict[str, Any]]:
    """All three probe surfaces, in render order."""
    return _min_canvas() + _full_canvas() + _inline_chart()


def is_probe_trigger(user_text: str | None) -> bool:
    """Exact match only, so no real conversation can trip it."""
    return bool(user_text) and user_text.strip().lower() in PROBE_TRIGGERS


def describe_echo(context: dict[str, Any]) -> str:
    """Reports what the Save button sent back, with Python types shown.

    The type is the point: a slider that writes ``"120"`` instead of ``120``
    renders fine and still breaks scoring.
    """
    lines = ["**Canvas probe — Save received**", ""]
    for key in ("name", "users"):
        value = context.get(key, "<missing>")
        lines.append(f"- `{key}` = `{value!r}` ({type(value).__name__})")
    extra = sorted(set(context) - {"name", "users", "prompt"})
    if extra:
        lines.append(f"- other keys: {', '.join(f'`{k}`' for k in extra)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Probe 4: live refresh and rich text (``probe live``)
# ---------------------------------------------------------------------------
#
# Answers two questions the workspace panel depends on:
#
# * Does an ``updateDataModel`` sent in a *later* message reach a canvas that
#   is already open? If so, the workspace can refresh in place after each
#   stage instead of opening a fresh copy.
# * How much markdown does ``Text`` render: headings, tables, quotes, links?
#   Decides whether document previews can show tables as-is.

LIVE_TRIGGERS = ("probe live", "a2ui probe live")
LIVE_SURFACE_ID = "probe-canvas-live"
PROBE_LIVE_BUMP = "probe_live_bump"

_LIVE_ROOT = "/ui/probe/live"

_RICH_MARKDOWN = """#### Heading 4

Body with **bold**, _italic_, `code` and a [link to google.com](https://www.google.com).

- bullet one
- bullet two

| Check | Verdict |
| :--- | :--- |
| 2.4 Transit approval | ✅ PASS |
| 3.5 Cloud policy | ❌ FAIL |

> A blockquote line.

- [x] done checkbox
- [ ] open checkbox"""


def build_live_probe() -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = [
        {
            "id": "root",
            "component": "Canvas",
            "children": ["p4-title", "p4-count", "p4-bar", "p4-bump", "p4-rule", "p4-md"],
            "autoOpen": True,
            "cardTitle": "Probe 4 — live refresh and rich text",
            "cardDescription": "Press Refresh, keep this panel open, watch the counter.",
            "cardIcon": "science",
        },
        {"id": "p4-title", "component": "Text", "text": "Probe 4", "variant": "h3"},
        {"id": "p4-count", "component": "Text", "text": {"path": f"{_LIVE_ROOT}/count"}, "variant": "h4"},
        {
            "id": "p4-bar",
            "component": "MaterialProgressBar",
            "value": {"path": f"{_LIVE_ROOT}/pct"},
            "mode": "determinate",
            "color": "primary",
            "ariaLabel": "Probe progress",
        },
        {"id": "p4-bump-label", "component": "Text", "text": "Refresh this panel"},
        {
            "id": "p4-bump",
            "component": "Button",
            "child": "p4-bump-label",
            "variant": "primary",
            "action": {
                "event": {
                    "name": PROBE_LIVE_BUMP,
                    "context": {"prompt": "Refresh — live probe", "n": {"path": f"{_LIVE_ROOT}/n"}},
                }
            },
        },
        {"id": "p4-rule", "component": "Divider"},
        {"id": "p4-md", "component": "Text", "text": _RICH_MARKDOWN, "variant": "body"},
    ]
    data = {"ui": {"probe": {"live": {"count": "Refreshed 0 times", "pct": 20, "n": 0}}}}
    return _surface(LIVE_SURFACE_ID, components, data)


def build_live_bump(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Data-only update to the already-open probe panel: no createSurface."""
    try:
        n = int(context.get("n") or 0) + 1
    except (TypeError, ValueError):
        n = 1
    return [
        build_patch(
            _LIVE_ROOT,
            {"count": f"Refreshed {n} time{'s' if n != 1 else ''}", "pct": min(100, 20 + 20 * n), "n": n},
            LIVE_SURFACE_ID,
        )
    ]


def is_live_trigger(user_text: str | None) -> bool:
    return bool(user_text) and user_text.strip().lower() in LIVE_TRIGGERS
