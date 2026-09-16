"""A2UI surfaces for Microsoft SharePoint sign-in.

Gemini Enterprise will not hand a Cloud Run A2A agent the end-user's Microsoft
token, so the user has to visit our `/auth` endpoint themselves. Until now that
was a markdown link buried in the reply text. This module renders it as a real
button instead.

The button depends on the A2UI ``openUrl`` client function, which is declared in
Gemini Enterprise's own composite catalog::

    "openUrl": {
      "properties": {
        "call": {"const": "openUrl"},
        "args": {"properties": {"url": {"type": "string", "format": "uri"}}}
      },
      "returnType": {"const": "void"}
    }

Being in the catalog is not proof the renderer implements it. Every other
component in `catalog.py` earned its place in ``GE_RENDER_VERIFIED`` by being
watched in the GE chat surface, and ``openUrl`` gets the same treatment:
:func:`build_openurl_probe` exists to settle the question before anything is
built on top of it.

Note the distinction this module has to respect. Everything verified so far uses
an ``event`` action, which travels to the server. ``openUrl`` is a
``functionCall``, which runs on the client and never reaches us. A renderer can
support one and ignore the other, so prior Button results say nothing here.
"""

from __future__ import annotations

from typing import Any

from qualify.a2ui.catalog import A2UI_VERSION, catalog_id

#: Kept off the main qualification surface so a probe cannot disturb a live form.
SIGNIN_SURFACE_ID = "signin"

#: Component ids are scoped to their surface, so reusing the conventional
#: ``root`` here does not collide with the qualification form's own root.
_PROBE_ROOT = "root"


def _create_surface(surface_id: str) -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "createSurface": {
            "surfaceId": surface_id,
            "catalogId": catalog_id(),
        },
    }


def _update_components(
    components: list[dict[str, Any]], surface_id: str
) -> dict[str, Any]:
    return {
        "version": A2UI_VERSION,
        "updateComponents": {
            "surfaceId": surface_id,
            "components": components,
        },
    }


def _open_url_action(url: str) -> dict[str, Any]:
    """A client-side action that opens ``url``.

    Unlike an ``event`` action this produces no server round trip, so a failure
    is silent. That is precisely why it needs probing rather than assuming.
    """
    return {"functionCall": {"call": "openUrl", "args": {"url": url}}}


def build_openurl_probe(
    auth_url: str, surface_id: str = SIGNIN_SURFACE_ID
) -> list[dict[str, Any]]:
    """A diagnostic surface that answers three questions in one round trip.

    Renders the same destination three ways:

    1. base ``Button`` with an ``openUrl`` action
    2. ``MaterialButton`` with an ``openUrl`` action
    3. ``Text`` holding a markdown link, the control

    The heading and the control are already known to render, so if the page is
    blank the surface itself failed and the buttons prove nothing. If the
    heading shows but a button does not, that component is unusable. If a button
    shows but clicking does nothing, the component renders and ``openUrl`` is
    ignored.
    """
    components: list[dict[str, Any]] = [
        {
            "id": _PROBE_ROOT,
            "component": "Column",
            "children": [
                "probe-title",
                "probe-rule-1",
                "probe-base-caption",
                "probe-base-button",
                "probe-rule-2",
                "probe-material-caption",
                "probe-material-button",
                "probe-rule-3",
                "probe-control-caption",
                "probe-control-link",
            ],
        },
        {
            "id": "probe-title",
            "component": "Text",
            "text": "A2UI openUrl probe",
            "variant": "h3",
        },
        {"id": "probe-rule-1", "component": "Divider"},
        {
            "id": "probe-base-caption",
            "component": "Text",
            "text": "1. Base Button, openUrl action",
            "variant": "caption",
        },
        {
            "id": "probe-base-label",
            "component": "Text",
            "text": "Sign in with Microsoft",
        },
        {
            "id": "probe-base-button",
            "component": "Button",
            "child": "probe-base-label",
            "variant": "primary",
            "action": _open_url_action(auth_url),
        },
        {"id": "probe-rule-2", "component": "Divider"},
        {
            "id": "probe-material-caption",
            "component": "Text",
            "text": "2. MaterialButton, openUrl action",
            "variant": "caption",
        },
        {
            "id": "probe-material-button",
            "component": "MaterialButton",
            "label": "Sign in with Microsoft",
            "variant": "raised",
            "color": "primary",
            "leadingIcon": "login",
            "action": _open_url_action(auth_url),
        },
        {"id": "probe-rule-3", "component": "Divider"},
        {
            "id": "probe-control-caption",
            "component": "Text",
            "text": "3. Control, markdown link in a Text component",
            "variant": "caption",
        },
        {
            "id": "probe-control-link",
            "component": "Text",
            "text": f"[Sign in with Microsoft]({auth_url})",
            "variant": "body",
        },
    ]

    return [
        _create_surface(surface_id),
        _update_components(components, surface_id),
    ]
