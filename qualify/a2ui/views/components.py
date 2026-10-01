"""Small builders for the A2UI components the views share.

Kept deliberately thin: each returns the plain dict GE expects, so a view
reads like the surface it produces.
"""

from __future__ import annotations

from typing import Any

from qualify.a2ui.compiler import (
    ROOT_ID,
    build_create_surface,
    build_patch,
    build_update_components,
)

Component = dict[str, Any]


def text(cid: str, value: str, variant: str = "body") -> Component:
    return {"id": cid, "component": "Text", "text": value, "variant": variant}


def divider(cid: str) -> Component:
    return {"id": cid, "component": "Divider"}


def column(cid: str, children: list[str]) -> Component:
    return {"id": cid, "component": "Column", "children": children}


def canvas_root(
    children: list[str],
    *,
    title: str,
    description: str,
    icon: str,
    auto_open: bool = True,
) -> Component:
    """A surface root that renders in GE's resizable side panel."""
    return {
        "id": ROOT_ID,
        "component": "Canvas",
        "children": children,
        "autoOpen": auto_open,
        "cardTitle": title,
        "cardDescription": description,
        "cardIcon": icon,
    }


def tabs(cid: str, items: list[tuple[str, str]]) -> Component:
    """``items`` is ``[(title, child_id), ...]``."""
    return {
        "id": cid,
        "component": "Tabs",
        "tabs": [{"title": t, "child": c} for t, c in items],
    }


def panel(
    cid: str,
    title: str,
    children: list[str],
    *,
    description: str | None = None,
    expanded: bool = False,
) -> Component:
    node: Component = {
        "id": cid,
        "component": "MaterialExpansionPanel",
        "title": title,
        "expanded": expanded,
        "children": children,
    }
    if description:
        node["description"] = description
    return node


def event_button(
    cid: str,
    label: str,
    event: str,
    context: dict[str, Any],
    *,
    primary: bool = False,
) -> list[Component]:
    """A base ``Button`` raising a server event; returns the label too.

    Base ``Button`` is the one whose events were watched reaching the agent
    (Phase 0 and the canvas probe), so it is used for every action.
    """
    label_id = f"{cid}-label"
    return [
        text(label_id, label, "body"),
        {
            "id": cid,
            "component": "Button",
            "child": label_id,
            "variant": "primary" if primary else "default",
            "action": {"event": {"name": event, "context": {"prompt": label, **context}}},
        },
    ]


def chart(cid: str, data_path: str, height: int = 340) -> Component:
    """A ``VegaChart`` whose spec lives in the data model.

    The catalog types ``spec`` as a DynamicValue, which admits a binding but
    not an inline object, so the spec is always passed by path.
    """
    return {"id": cid, "component": "VegaChart", "spec": {"path": data_path}, "height": height}


def table(
    cid: str,
    columns: list[tuple[str, str]],
    rows: list[dict[str, Any]],
    caption: str | None = None,
) -> Component:
    """``columns`` is ``[(header, field), ...]``. Cells are sent as strings,
    which is what the catalog requires.

    Columns are deliberately not sortable: in GE a header click sends a
    ``fetchData`` action to the agent (server-side sort), which turns into a
    chat turn. Rows are already sent in the intended order.
    """
    node: Component = {
        "id": cid,
        "component": "GcbpTable",
        "columns": [{"header": h, "field": f, "sortable": False} for h, f in columns],
        "rows": [{k: "" if v is None else str(v) for k, v in r.items()} for r in rows],
    }
    if caption:
        node["caption"] = caption
    return node


def surface(
    surface_id: str,
    components: list[Component],
    data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create, then components, then data: the order GE requires."""
    messages = [
        build_create_surface(surface_id),
        build_update_components(components, surface_id),
    ]
    if data is not None:
        messages.append(build_patch("/", data, surface_id))
    return messages
