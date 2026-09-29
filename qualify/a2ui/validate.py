"""Validating compiler output against the vendored A2UI schemas.

Two layers of checking, because each catches a different class of mistake.

**Schema validation** catches malformed messages: a missing `version`, a
component that does not exist, a prop the catalog never declared. It runs
entirely offline against the vendored files, so the tests do not quietly pass
when a CDN is unreachable.

**Structural validation** catches the mistakes schemas cannot see: a child id
that names no component, an orphan component nothing points at, a `{"path":
...}` binding that resolves to no field on the record. All three produce
valid JSON and a broken form.

The second layer is the one that has caught real bugs. The first is table
stakes.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from qualify.a2ui.catalog import (
    load_catalog,
    load_common_types,
    load_server_to_client,
)
from qualify.schema.paths import PathError, is_ui_path, resolve_record_path


class SurfaceError(ValueError):
    """Raised when a compiled surface is internally inconsistent."""


#: Where `server_to_client.json` and `common_types.json` expect to find "the"
#: catalog.
#:
#: Both schemas `$ref` it as the bare relative name `catalog.json`, which
#: resolves against their own `$id` to this URL. The GE composite catalog
#: publishes itself under a gstatic URL instead, so without an alias the
#: envelope schema cannot resolve `catalog.json#/$defs/anyComponent` and every
#: `updateComponents` message fails to validate for the wrong reason.
#:
#: Aliasing the composite here is also the behaviour we want: it makes the
#: envelope validate components against the catalog GE actually renders,
#: rather than the generic standard catalog.
CATALOG_ALIAS = "https://a2ui.org/specification/v0_9/catalog.json"


def _registry() -> Registry:
    """A `$ref` registry holding the three vendored schemas.

    The schemas cross-reference each other by absolute URL. Without
    registering them, jsonschema tries to fetch them and the tests become
    network-dependent — and worse, quietly pass or fail based on connectivity.
    """
    common = Resource.from_contents(load_common_types())
    catalog = Resource.from_contents(load_catalog())
    envelope = Resource.from_contents(load_server_to_client())

    return Registry().with_resources(
        [
            (load_common_types()["$id"], common),
            (load_catalog()["$id"], catalog),
            (load_server_to_client()["$id"], envelope),
            (CATALOG_ALIAS, catalog),
        ]
    )


def message_validator() -> Draft202012Validator:
    """Validates a whole A2UI message envelope."""
    return Draft202012Validator(load_server_to_client(), registry=_registry())


def component_validator() -> Draft202012Validator:
    """Validates one component against the GE composite catalog.

    Redundant with the envelope schema, which reaches the same `anyComponent`
    definition. Kept because the error it produces names the offending
    component, where the envelope error points at an array index inside a
    `oneOf` branch and is close to unreadable.
    """
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"{CATALOG_ALIAS}#/$defs/anyComponent",
    }
    return Draft202012Validator(schema, registry=_registry())


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def _walk_bindings(node: Any, out: list[str]) -> None:
    """Collects every `{"path": ...}` data binding in a component tree."""
    if isinstance(node, dict):
        if set(node) == {"path"} and isinstance(node["path"], str):
            out.append(node["path"])
            return
        for value in node.values():
            _walk_bindings(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_bindings(item, out)


def check_component_graph(components: list[dict[str, Any]], root: str = "root") -> None:
    """Every referenced id exists, every component is reachable, ids are unique.

    An unreachable component is not a rendering error — the renderer simply
    ignores it. So a field that fell out of the root's `children` list
    disappears from the form with no warning anywhere. This is the check that
    notices.
    """
    ids = [c["id"] for c in components]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise SurfaceError(f"duplicate component ids: {sorted(duplicates)}")

    by_id = {c["id"]: c for c in components}
    if root not in by_id:
        raise SurfaceError(f"no component with id {root!r}.")

    referenced: set[str] = set()
    for component in components:
        for child in _child_ids(component):
            if child not in by_id:
                raise SurfaceError(
                    f"{component['id']!r} references child {child!r}, "
                    f"which is not in the component list."
                )
            referenced.add(child)

    orphans = set(by_id) - referenced - {root}
    if orphans:
        raise SurfaceError(
            f"components are never referenced and will not render: "
            f"{sorted(orphans)}"
        )


def _child_ids(component: dict[str, Any]) -> list[str]:
    out: list[str] = []
    children = component.get("children")
    if isinstance(children, list):
        out.extend(c for c in children if isinstance(c, str))
    elif isinstance(children, dict) and "componentId" in children:
        out.append(children["componentId"])

    child = component.get("child")
    if isinstance(child, str):
        out.append(child)

    for tab in component.get("tabs", []) or []:
        for key in ("child", "content"):
            if isinstance(tab.get(key), str):
                out.append(tab[key])

    return out


def check_bindings(components: list[dict[str, Any]]) -> list[str]:
    """Every `/uc/...` binding resolves to a real record field.

    Returns the bindings it checked, so a test can assert the form actually
    binds something rather than passing on an empty tree.
    """
    paths: list[str] = []
    _walk_bindings(components, paths)

    for path in paths:
        if is_ui_path(path) or path == "/uc" or path == "/":
            continue
        try:
            resolve_record_path(path)
        except PathError as exc:
            raise SurfaceError(f"binding {path!r} does not resolve: {exc}") from exc

    return paths


def validate_surface(messages: list[dict[str, Any]]) -> None:
    """Runs every check over a full `build_surface` output."""
    validator = message_validator()
    comp_validator = component_validator()

    for message in messages:
        errors = sorted(validator.iter_errors(message), key=lambda e: e.json_path)
        if errors:
            first = errors[0]
            raise SurfaceError(
                f"message failed the A2UI envelope schema at "
                f"{first.json_path}: {first.message}"
            )

        update = message.get("updateComponents")
        if not update:
            continue

        components = update["components"]
        for component in components:
            errors = sorted(
                comp_validator.iter_errors(component), key=lambda e: e.json_path
            )
            if errors:
                first = errors[0]
                raise SurfaceError(
                    f"component {component.get('id')!r} "
                    f"({component.get('component')}) failed the catalog "
                    f"schema at {first.json_path}: {first.message}"
                )

        check_component_graph(components)
        check_bindings(components)
