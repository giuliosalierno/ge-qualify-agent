"""Access to the vendored A2UI v0.9 schemas.

Three files are vendored under `catalogs/`, all byte-for-byte copies of what
Google ships:

| File | Source | Used for |
| :--- | :--- | :--- |
| `ge_composite_v0_9.json` | the reference GE sample agent | which components exist and what props they take |
| `common_types_v0_9.json` | `a2ui` SDK `assets/0.9/` | `$ref` targets the catalog points at |
| `server_to_client_v0_9.json` | `a2ui` SDK `assets/0.9/` | the message envelope shape |

They are vendored rather than fetched because the golden tests must run with
no network. A test suite that silently passes when a CDN is unreachable is
worse than no test suite.

The catalog ships inside the wheel, so `Path(__file__).parent` resolves at
runtime as well as in the source tree.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

#: The version string that goes in every outbound A2UI message.
#:
#: Deliberately not the SDK's own constant, which is `"0.9"`. The wire format
#: wants `"v0.9"`. Phase 0 confirmed GE rejects the bare number.
A2UI_VERSION = "v0.9"

CATALOG_DIR = Path(__file__).parent / "catalogs"

GE_COMPOSITE_PATH = CATALOG_DIR / "ge_composite_v0_9.json"
COMMON_TYPES_PATH = CATALOG_DIR / "common_types_v0_9.json"
SERVER_TO_CLIENT_PATH = CATALOG_DIR / "server_to_client_v0_9.json"


@lru_cache(maxsize=None)
def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog() -> dict[str, Any]:
    """The Gemini Enterprise composite catalog."""
    return _load(GE_COMPOSITE_PATH)


def load_common_types() -> dict[str, Any]:
    return _load(COMMON_TYPES_PATH)


def load_server_to_client() -> dict[str, Any]:
    return _load(SERVER_TO_CLIENT_PATH)


@lru_cache(maxsize=None)
def catalog_id() -> str:
    """The `catalogId` that `createSurface` must declare.

    Read from the file rather than hard-coded so that swapping in a newer
    catalog cannot leave a stale identifier behind.
    """
    return load_catalog()["catalogId"]


@lru_cache(maxsize=None)
def component_names() -> frozenset[str]:
    return frozenset(load_catalog()["components"])


@lru_cache(maxsize=None)
def function_names() -> frozenset[str]:
    return frozenset(load_catalog()["functions"])


def component_schema(name: str) -> dict[str, Any]:
    return load_catalog()["components"][name]


# ---------------------------------------------------------------------------
# What Gemini Enterprise is actually known to do
# ---------------------------------------------------------------------------
#
# Two different facts, tracked separately, because the L12 test proved they
# come apart:
#
#   rendering  — the component appeared on screen
#   binding    — the component wrote the type we expected into the data model
#
# `ChoicePicker` is the cautionary case. It renders perfectly and still fails
# us, because it writes `["x"]` where the record needs `"x"`. Collapsing these
# two sets into one "verified" list is how that trap gets walked into twice.

#: Components observed rendering in the GE chat surface.
#:
#: The five base ones come from the Phase 0 echo probe. The five Material ones
#: come from the L12 probe on 2026-09-15, which also showed base and Material
#: mix freely in a single surface.
GE_RENDER_VERIFIED = frozenset(
    {
        # Base — Phase 0
        "Column",
        "Text",
        "TextField",
        "Button",
        "Divider",
        # Material — L12 probe
        "MaterialText",
        "MaterialSelect",
        "MaterialRadioButton",
        "MaterialButtonToggle",
        "MaterialChips",
        # Material — openUrl probe, 2026-09-16. Rendered with `variant`,
        # `color`, and `leadingIcon` set. Only the drawing is attested here;
        # its `action` was a client-side `openUrl`, never an `event`, so this
        # says nothing about whether it dispatches to the server.
        "MaterialButton",
        # Canvas probe, 2026-10-01 (`probe canvas`, qualify/a2ui/canvas_probe.py).
        # All drawn in GE, including inside a Canvas side panel and its Tabs.
        # `VegaChart.spec` was passed by data binding: the catalog types it as
        # a DynamicValue, which does not admit an inline object. The Save
        # button's event reached the agent, but the slider's write-back type
        # was not recorded, so nothing here is added to GE_BIND_VERIFIED.
        "Canvas",
        "Tabs",
        "VegaChart",
        "GcbpTable",
        "MaterialExpansionPanel",
        "MaterialBadge",
        "MaterialSlider",
    }
)

#: Components observed writing the expected type back to the agent.
#:
#: Stricter than rendering, and the only set that matters for inputs. Each
#: entry here was watched putting a real value into an action context.
GE_BIND_VERIFIED = frozenset(
    {
        "TextField",  # Phase 0
        "Button",  # Phase 0
        "MaterialSelect",  # L12: wrote "wb_custom_mcp"
        "MaterialRadioButton",  # L12: wrote "high_code_agent"
        "MaterialButtonToggle",  # L12: wrote "pro_code"
        "MaterialChips",  # L12: wrote "low_code"
    }
)

#: Components whose `value` is a scalar `DynamicString` per the catalog.
#:
#: This is what makes enum and closed-vocabulary fields collectable at all.
#: All four were verified end to end in the L12 probe: each wrote a plain
#: JSON string, not a list.
#:
#: `ChoicePicker` is deliberately absent. It is a single-select in
#: `mutuallyExclusive` mode and still writes a list, which is the entire
#: reason this set has to exist.
SCALAR_SINGLE_SELECT = frozenset(
    {
        "MaterialSelect",
        "MaterialRadioButton",
        "MaterialButtonToggle",
        "MaterialChips",
    }
)

#: Rendered in GE, but never seen writing a value.
#:
#: `MaterialText` is display-only, so there is nothing to test. Anything else
#: landing here should be treated as unusable for input until watched.
GE_BIND_UNTESTED = frozenset({"MaterialText"})

#: In the catalog, allowed in packs, but never exercised in GE at all.
GE_UNVERIFIED_COMPONENTS = frozenset(
    {
        "ChoicePicker",
        "CheckBox",
        "DateTimeInput",
        "Card",
    }
)

#: Everything a pack may name.
PACK_ALLOWED_COMPONENTS = GE_RENDER_VERIFIED | GE_UNVERIFIED_COMPONENTS

#: Components a pack may bind to a writable field.
#:
#: Display-only components are excluded, as is anything whose binding has not
#: been watched. A component that renders but silently drops the user's input
#: is worse than one that fails outright, because the form looks like it
#: worked.
PACK_WRITABLE_COMPONENTS = (
    GE_BIND_VERIFIED | GE_UNVERIFIED_COMPONENTS
) - GE_BIND_UNTESTED
