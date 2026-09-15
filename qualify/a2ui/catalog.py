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
# Components confirmed to render in Gemini Enterprise
# ---------------------------------------------------------------------------

#: The base (non-Material) components the Phase 0 echo probe actually painted
#: in the GE chat surface.
#:
#: The catalog offers 52 components, but "in the catalog" and "renders in GE"
#: are different claims and only the second one matters. These five were
#: observed working first-hand. Everything else is untested, so the pack
#: loader refuses it rather than letting a demo fail in front of a customer.
#:
#: Widen this set only after watching the component render in GE.
GE_VERIFIED_COMPONENTS = frozenset(
    {
        "Column",
        "Text",
        "TextField",
        "Button",
        "Divider",
    }
)

#: Components we rely on that are in the catalog but have NOT been seen
#: rendering in GE. Allowed in packs, listed here so the risk stays visible.
GE_UNVERIFIED_COMPONENTS = frozenset(
    {
        "ChoicePicker",
        "CheckBox",
        "DateTimeInput",
        "Card",
    }
)

#: Everything a pack may name.
PACK_ALLOWED_COMPONENTS = GE_VERIFIED_COMPONENTS | GE_UNVERIFIED_COMPONENTS
