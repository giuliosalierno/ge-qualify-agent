"""Resolves data-source names against the Gemini Enterprise connector catalog.

The catalog (``qualify/scoring/connectors.yaml``) records, for every source the
agent may meet, whether GE supports it natively, whether its connector has
documented end-user actions, and the official page that says so. The
capability classifier reads it instead of hard-coded keyword lists, so every
"native connector" claim in a rationale can be traced to a citation.

Anything the catalog cannot place is ``unverified`` and treated like a custom
integration: the anti-overcommitment rule never promises a native connector it
cannot cite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

CATALOG_PATH = Path(__file__).resolve().parent / "connectors.yaml"

Status = Literal["native", "custom", "unverified"]


@dataclass(frozen=True)
class ConnectorMatch:
    """One data source as the classifier sees it."""

    source: str  #: what the record said, e.g. ``internal_api`` or ``Tagetik``
    id: str | None  #: catalog id, or None when nothing matched
    label: str
    status: Status
    actions: bool  #: connector page documents end-user (write) actions
    doc: str | None
    note: str | None = None

    @property
    def native(self) -> bool:
        return self.status == "native"


@dataclass(frozen=True)
class Catalog:
    reviewed: str
    source_index: str
    custom_mcp_doc: str
    entries: dict[str, dict]
    #: ``(alias, id)`` sorted longest alias first, then catalog order.
    aliases: tuple[tuple[str, str], ...]


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[_\-/]+", " ", text.strip().lower()).split())


@lru_cache(maxsize=1)
def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries: dict[str, dict] = data["connectors"]
    pairs: list[tuple[int, int, str, str]] = []
    for order, (cid, entry) in enumerate(entries.items()):
        for alias in {cid, *entry.get("aliases", [])}:
            norm = _normalise(alias)
            pairs.append((-len(norm), order, norm, cid))
    pairs.sort()
    return Catalog(
        reviewed=str(data["reviewed"]),
        source_index=data["source_index"],
        custom_mcp_doc=data["custom_mcp_doc"],
        entries=entries,
        aliases=tuple((norm, cid) for _, _, norm, cid in pairs),
    )


def resolve(source: str, catalog: Catalog | None = None) -> ConnectorMatch:
    """Places one source name in the catalog.

    Exact id first (the pack's option values), then the longest alias found
    as whole words — so "Oracle NetSuite" is NetSuite, not Oracle, and
    "Cloud SQL" is not a self-hosted SQL database.
    """
    cat = catalog or load_catalog()
    raw = source.strip()
    key = raw.lower()
    cid: str | None = key if key in cat.entries else None
    if cid is None:
        norm = _normalise(raw)
        for alias, alias_id in cat.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", norm):
                cid = alias_id
                break

    if cid is None:
        return ConnectorMatch(
            source=raw,
            id=None,
            label=raw,
            status="unverified",
            actions=False,
            doc=None,
            note="Not found in the Gemini Enterprise connector catalog.",
        )

    entry = cat.entries[cid]
    status: Status = entry["status"]
    return ConnectorMatch(
        source=raw,
        id=cid,
        label=entry["label"],
        status=status,
        actions=bool(entry.get("actions", False)),
        doc=entry.get("doc") or (cat.custom_mcp_doc if status == "custom" else None),
        note=entry.get("note"),
    )


def catalog_marker(catalog: Catalog | None = None) -> str:
    """Stamped into every rationale. A different date means "re-classify"."""
    cat = catalog or load_catalog()
    return f"Connector catalog reviewed {cat.reviewed}"
