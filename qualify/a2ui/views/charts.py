"""Vega-Lite specs for the portfolio views.

The quadrant matrix is the visual centre of the demo: business value on y,
feasibility on x, bubble size for hours saved, colour for quadrant.

Scores are integers 1–5. The quadrant boundaries from
``scoring.portfolio._assign_quadrant`` (value >= 3, feasibility >= 4, and
feasibility >= 2 for a Strategic Bet) are therefore drawn *between* integers,
at 2.5, 3.5 and 1.5. Every score cell then sits wholly inside one shaded
region, and points spread within a cell can never cross a boundary — which
an earlier version, with the lines on 3 and 4, did.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from qualify.scoring.portfolio import QUADRANT_ORDER, OpportunityEvaluation

#: Minimum scores, as used by ``_assign_quadrant``.
VALUE_THRESHOLD = 3
FEASIBILITY_THRESHOLD = 4
STRATEGIC_MIN_FEASIBILITY = 2

#: Boundaries as drawn: halfway between the integer scores.
VALUE_LINE = VALUE_THRESHOLD - 0.5
FEASIBILITY_LINE = FEASIBILITY_THRESHOLD - 0.5
STRATEGIC_LINE = STRATEGIC_MIN_FEASIBILITY - 0.5

#: Material palette, one colour per quadrant, in ``QUADRANT_ORDER``.
QUADRANT_COLOURS = {
    "Quick Wins": "#1e8e3e",
    "Strategic Bets": "#1a73e8",
    "Departmental Niche": "#e37400",
    "Deprioritized": "#80868b",
}

#: Overall chart size in pixels, axes and labels included ("fit" autosize).
#: Sized for GE's default side-panel width.
CHART_WIDTH = 600
CHART_HEIGHT = 420

_LO, _HI = 0.5, 5.5
#: Largest offset from the cell centre. Below 0.5, so a spread point stays in
#: its cell and therefore in its quadrant.
_SPREAD = 0.28
_NAME_MAX = 24


def _short(name: str) -> str:
    return name if len(name) <= _NAME_MAX else name[: _NAME_MAX - 1].rstrip() + "…"


def matrix_points(
    evaluations: list[OpportunityEvaluation] | tuple[OpportunityEvaluation, ...],
    highlight_id: str | None = None,
) -> list[dict[str, Any]]:
    """One row per opportunity, ready for the chart.

    Points sharing a cell are spread on a small circle inside it,
    deterministically, so none hides another and every render is identical.
    """
    cells: dict[tuple[int, int], list[OpportunityEvaluation]] = defaultdict(list)
    for ev in sorted(evaluations, key=lambda e: e.record_id):
        cells[(ev.feasibility_score, ev.business_value_score)].append(ev)

    points: list[dict[str, Any]] = []
    for (feas, value), group in cells.items():
        for i, ev in enumerate(group):
            if len(group) == 1:
                dx = dy = 0.0
            else:
                # Start at 12 o'clock so a pair splits vertically and the
                # labels (drawn above each bubble) do not collide.
                angle = math.pi / 2 + 2 * math.pi * i / len(group)
                dx, dy = _SPREAD * math.cos(angle), _SPREAD * math.sin(angle)
            label = _short(ev.initiative_name)
            if ev.has_hard_blocker:
                label = f"⛔ {label}"
            points.append(
                {
                    "name": ev.initiative_name,
                    "label": label,
                    "recordId": ev.record_id,
                    "x": round(feas + dx, 3),
                    "y": round(value + dy, 3),
                    "value": value,
                    "feasibility": feas,
                    "indicative": "yes (pending tech review)"
                    if ev.is_indicative_feasibility
                    else "no",
                    "hours": round(ev.annual_hours_saved or 0),
                    "users": ev.user_count or 0,
                    "quadrant": ev.quadrant,
                    "status": ev.priority_status,
                    "blocked": ev.has_hard_blocker,
                    "highlight": ev.record_id == highlight_id,
                }
            )
    points.sort(key=lambda p: p["recordId"])
    return points


def _region(x0: float, x1: float, y0: float, y1: float, quadrant: str) -> dict[str, Any]:
    return {"x": x0, "x2": x1, "y": y0, "y2": y1, "quadrant": quadrant}


def quadrant_regions() -> list[dict[str, Any]]:
    """The four quadrants as rectangles, matching ``_assign_quadrant``."""
    return [
        _region(FEASIBILITY_LINE, _HI, VALUE_LINE, _HI, "Quick Wins"),
        _region(STRATEGIC_LINE, FEASIBILITY_LINE, VALUE_LINE, _HI, "Strategic Bets"),
        _region(FEASIBILITY_LINE, _HI, _LO, VALUE_LINE, "Departmental Niche"),
        _region(_LO, STRATEGIC_LINE, VALUE_LINE, _HI, "Deprioritized"),
        _region(_LO, FEASIBILITY_LINE, _LO, VALUE_LINE, "Deprioritized"),
    ]


def quadrant_matrix_spec(
    points: list[dict[str, Any]], width: int = CHART_WIDTH, height: int = CHART_HEIGHT
) -> dict[str, Any]:
    """Value x feasibility bubble chart with shaded, labelled quadrants.

    Fixed size on purpose. With ``"width": "container"`` Vega measures its
    parent once, at first draw. GE draws the chart while the side panel is
    still opening, so the first render came out narrow and only corrected
    itself when a tab switch forced a redraw. A fixed width that fits the
    default panel renders the same every time.
    """
    region_labels = [
        {"x": _HI - 0.08, "y": _HI - 0.12, "label": "QUICK WINS", "quadrant": "Quick Wins", "align": "right"},
        {"x": STRATEGIC_LINE + 0.08, "y": _HI - 0.12, "label": "STRATEGIC BETS", "quadrant": "Strategic Bets", "align": "left"},
        {"x": _HI - 0.08, "y": _LO + 0.12, "label": "DEPARTMENTAL NICHE", "quadrant": "Departmental Niche", "align": "right"},
        {"x": _LO + 0.08, "y": _LO + 0.12, "label": "DEPRIORITIZED", "quadrant": "Deprioritized", "align": "left"},
    ]
    colour = {
        "field": "quadrant",
        "type": "nominal",
        "scale": {
            "domain": list(QUADRANT_ORDER),
            "range": [QUADRANT_COLOURS[q] for q in QUADRANT_ORDER],
        },
    }
    axis_scale = {"domain": [_LO, _HI], "nice": False, "zero": False}
    x_axis = {
        "field": "x",
        "type": "quantitative",
        "title": "Technical feasibility →",
        "scale": axis_scale,
        "axis": {"values": [1, 2, 3, 4, 5], "grid": False, "tickSize": 0, "domain": False, "labelPadding": 6},
    }
    y_axis = {
        "field": "y",
        "type": "quantitative",
        "title": "Business value →",
        "scale": axis_scale,
        "axis": {"values": [1, 2, 3, 4, 5], "grid": False, "tickSize": 0, "domain": False, "labelPadding": 6},
    }
    plain_x = {"field": "x", "type": "quantitative", "scale": axis_scale}
    plain_y = {"field": "y", "type": "quantitative", "scale": axis_scale}

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": width,
        "height": height,
        "autosize": {"type": "fit", "contains": "padding"},
        "config": {
            "view": {"stroke": None},
            "axis": {"labelFontSize": 12, "titleFontSize": 13, "titleFontWeight": "normal", "titleColor": "#5f6368"},
            "legend": {"orient": "bottom", "direction": "horizontal", "labelFontSize": 12, "symbolSize": 140},
        },
        "layer": [
            # Shaded quadrants.
            {
                "data": {"values": quadrant_regions()},
                "mark": {"type": "rect", "opacity": 0.09},
                "encoding": {
                    "x": x_axis,
                    "x2": {"field": "x2"},
                    "y": y_axis,
                    "y2": {"field": "y2"},
                    "color": {**colour, "legend": None},
                },
            },
            # Quadrant names in their corners, tinted like the quadrant.
            {
                "data": {"values": region_labels},
                "mark": {"type": "text", "fontSize": 11, "fontWeight": "bold", "opacity": 0.7, "baseline": "middle"},
                "encoding": {
                    "x": plain_x,
                    "y": plain_y,
                    "text": {"field": "label"},
                    "align": {"field": "align"},
                    "color": {**colour, "legend": None},
                },
            },
            # Boundaries.
            {
                "data": {"values": [{"x": FEASIBILITY_LINE}]},
                "mark": {"type": "rule", "strokeDash": [5, 4], "color": "#9aa0a6"},
                "encoding": {"x": plain_x},
            },
            {
                "data": {"values": [{"y": VALUE_LINE}]},
                "mark": {"type": "rule", "strokeDash": [5, 4], "color": "#9aa0a6"},
                "encoding": {"y": plain_y},
            },
            # Bubbles. Index 4: tests and views read the points from here.
            {
                "data": {"values": points},
                "mark": {"type": "circle", "opacity": 0.85, "stroke": "white", "strokeWidth": 1.5},
                "encoding": {
                    "x": plain_x,
                    "y": plain_y,
                    "size": {
                        "field": "hours",
                        "type": "quantitative",
                        "scale": {"range": [120, 1500], "zero": True},
                        "legend": None,
                    },
                    "color": {**colour, "title": None},
                    "stroke": {
                        "condition": {"test": "datum.highlight", "value": "#202124"},
                        "value": "white",
                    },
                    "strokeWidth": {
                        "condition": {"test": "datum.highlight", "value": 3},
                        "value": 1.5,
                    },
                    "tooltip": [
                        {"field": "name", "type": "nominal", "title": "Initiative"},
                        {"field": "recordId", "type": "nominal", "title": "Record"},
                        {"field": "quadrant", "type": "nominal", "title": "Quadrant"},
                        {"field": "value", "type": "quantitative", "title": "Business value"},
                        {"field": "feasibility", "type": "quantitative", "title": "Feasibility"},
                        {"field": "indicative", "type": "nominal", "title": "Indicative"},
                        {"field": "hours", "type": "quantitative", "title": "Hours / yr", "format": ","},
                        {"field": "users", "type": "quantitative", "title": "Users", "format": ","},
                        {"field": "status", "type": "nominal", "title": "Status"},
                    ],
                },
            },
            # Names under each bubble; bold for the highlighted one.
            {
                "data": {"values": points},
                "mark": {"type": "text", "baseline": "top", "dy": 24, "fontSize": 11, "color": "#3c4043"},
                "encoding": {
                    "x": plain_x,
                    "y": plain_y,
                    "text": {"field": "label"},
                    "fontWeight": {
                        "condition": {"test": "datum.highlight", "value": "bold"},
                        "value": "normal",
                    },
                },
            },
        ],
    }
