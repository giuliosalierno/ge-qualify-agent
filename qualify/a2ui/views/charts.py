"""Vega-Lite specs for the portfolio views.

The quadrant matrix is the visual centre of the demo: business value on y,
feasibility on x, bubble size for hours saved, colour for quadrant. The
shaded regions and dashed thresholds come straight from
``scoring.portfolio._assign_quadrant`` (value >= 3, feasibility >= 4), so the
picture can never disagree with the scoring.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from qualify.scoring.portfolio import QUADRANT_ORDER, OpportunityEvaluation

#: Thresholds used by ``_assign_quadrant``.
VALUE_THRESHOLD = 3
FEASIBILITY_THRESHOLD = 4

#: Material palette, one colour per quadrant, in ``QUADRANT_ORDER``.
QUADRANT_COLOURS = {
    "Quick Wins": "#1e8e3e",
    "Strategic Bets": "#1a73e8",
    "Departmental Niche": "#f9ab00",
    "Deprioritized": "#9aa0a6",
}

_AXIS_MAX = 5.6
_JITTER = 0.18


def matrix_points(
    evaluations: list[OpportunityEvaluation] | tuple[OpportunityEvaluation, ...],
    highlight_id: str | None = None,
) -> list[dict[str, Any]]:
    """One row per opportunity, ready for the chart.

    Scores are integers, so several opportunities often share a cell. Points
    sharing a cell are spread on a small circle, deterministically, so none
    hides another and the chart is identical on every render.
    """
    cells: dict[tuple[int, int], list[OpportunityEvaluation]] = defaultdict(list)
    for ev in evaluations:
        cells[(ev.feasibility_score, ev.business_value_score)].append(ev)

    points: list[dict[str, Any]] = []
    for (feas, value), group in cells.items():
        for i, ev in enumerate(group):
            if len(group) == 1:
                dx = dy = 0.0
            else:
                angle = 2 * math.pi * i / len(group)
                dx, dy = _JITTER * math.cos(angle), _JITTER * math.sin(angle)
            points.append(
                {
                    "name": ev.initiative_name,
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
                    "highlight": ev.record_id == highlight_id,
                }
            )
    points.sort(key=lambda p: p["recordId"])
    return points


def _region(x0: float, x1: float, y0: float, y1: float, quadrant: str) -> dict[str, Any]:
    return {"x": x0, "x2": x1, "y": y0, "y2": y1, "quadrant": quadrant}


def quadrant_matrix_spec(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Value x feasibility bubble chart with shaded quadrant regions."""
    regions = [
        _region(FEASIBILITY_THRESHOLD, _AXIS_MAX, VALUE_THRESHOLD, _AXIS_MAX, "Quick Wins"),
        _region(2, FEASIBILITY_THRESHOLD, VALUE_THRESHOLD, _AXIS_MAX, "Strategic Bets"),
        _region(FEASIBILITY_THRESHOLD, _AXIS_MAX, 0, VALUE_THRESHOLD, "Departmental Niche"),
    ]
    labels = [
        {"x": _AXIS_MAX - 0.05, "y": _AXIS_MAX - 0.1, "label": "Quick Wins", "align": "right"},
        {"x": 2.05, "y": _AXIS_MAX - 0.1, "label": "Strategic Bets", "align": "left"},
        {"x": _AXIS_MAX - 0.05, "y": 0.2, "label": "Departmental Niche", "align": "right"},
    ]
    colour_scale = {
        "domain": list(QUADRANT_ORDER),
        "range": [QUADRANT_COLOURS[q] for q in QUADRANT_ORDER],
    }
    axis_scale = {"domain": [0, _AXIS_MAX], "nice": False}

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": "container",
        "layer": [
            {
                "data": {"values": regions},
                "mark": {"type": "rect", "opacity": 0.08},
                "encoding": {
                    "x": {"field": "x", "type": "quantitative", "scale": axis_scale},
                    "x2": {"field": "x2"},
                    "y": {"field": "y", "type": "quantitative", "scale": axis_scale},
                    "y2": {"field": "y2"},
                    "color": {"field": "quadrant", "type": "nominal", "scale": colour_scale, "legend": None},
                },
            },
            {
                "data": {"values": labels},
                "mark": {"type": "text", "fontSize": 11, "fontWeight": "bold", "opacity": 0.55, "baseline": "middle"},
                "encoding": {
                    "x": {"field": "x", "type": "quantitative"},
                    "y": {"field": "y", "type": "quantitative"},
                    "text": {"field": "label"},
                    "align": {"field": "align"},
                },
            },
            {
                "data": {"values": [{"x": FEASIBILITY_THRESHOLD}]},
                "mark": {"type": "rule", "strokeDash": [4, 4], "color": "#9aa0a6"},
                "encoding": {"x": {"field": "x", "type": "quantitative"}},
            },
            {
                "data": {"values": [{"y": VALUE_THRESHOLD}]},
                "mark": {"type": "rule", "strokeDash": [4, 4], "color": "#9aa0a6"},
                "encoding": {"y": {"field": "y", "type": "quantitative"}},
            },
            {
                "data": {"values": points},
                "mark": {"type": "circle", "opacity": 0.85, "stroke": "#202124"},
                "encoding": {
                    "x": {
                        "field": "x",
                        "type": "quantitative",
                        "title": "Technical feasibility (1–5)",
                        "scale": axis_scale,
                    },
                    "y": {
                        "field": "y",
                        "type": "quantitative",
                        "title": "Business value (1–5)",
                        "scale": axis_scale,
                    },
                    "size": {
                        "field": "hours",
                        "type": "quantitative",
                        "title": "Hours saved / yr",
                        "scale": {"range": [90, 1600], "zero": True},
                    },
                    "color": {
                        "field": "quadrant",
                        "type": "nominal",
                        "title": "Quadrant",
                        "scale": colour_scale,
                    },
                    "strokeWidth": {
                        "condition": {"test": "datum.highlight", "value": 3},
                        "value": 0,
                    },
                    "tooltip": [
                        {"field": "name", "type": "nominal", "title": "Initiative"},
                        {"field": "recordId", "type": "nominal", "title": "Record"},
                        {"field": "value", "type": "quantitative", "title": "Value"},
                        {"field": "feasibility", "type": "quantitative", "title": "Feasibility"},
                        {"field": "indicative", "type": "nominal", "title": "Indicative"},
                        {"field": "hours", "type": "quantitative", "title": "Hours / yr", "format": ","},
                        {"field": "users", "type": "quantitative", "title": "Users", "format": ","},
                        {"field": "status", "type": "nominal", "title": "Status"},
                    ],
                },
            },
            {
                "data": {"values": points},
                "transform": [{"filter": "datum.highlight"}],
                "mark": {"type": "text", "dy": -18, "fontWeight": "bold"},
                "encoding": {
                    "x": {"field": "x", "type": "quantitative"},
                    "y": {"field": "y", "type": "quantitative"},
                    "text": {"field": "name"},
                },
            },
        ],
    }
