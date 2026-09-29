"""Extracting the Systems & Data Landscape matrix from conversation.

The matrix is the one part of the technical review that cannot be a form. Each
row is a `SystemEntry`, so `technical.systems` is a `list[BaseModel]`, and the
pack loader refuses it outright:

    assert_pack_path('/uc/technical/systems')
    PathError: stops on a container, not a value

Only `ChoicePicker` handles lists, and it writes `list[str]`. No component in
the verified catalog edits repeating rows, and the stage extractor is equally
blind — `build_schema` derives its response schema from the pack's scalar
fields.

So the matrix is built from what the reviewer says, and shown back to them as a
locked table they can correct in chat. That is slower than typing into a grid,
and it is the honest option: the alternative is a component nobody has watched
render, holding data nobody has watched save.

The same discipline as `patcher.py` applies. The model reports only what was
said, quotes its evidence, and returning nothing is a correct answer.
"""

from __future__ import annotations

import logging
from typing import Any

from qualify.schema.use_case_record import SystemEntry, UseCaseRecord

log = logging.getLogger(__name__)

#: The stage the matrix is collected in. Running this on every stage would pay
#: for a model call four extra times to re-extract facts already captured.
SYSTEMS_STAGE_ID = "systems"

#: Where the rendered table is bound. A `/ui` path, so it is compiler-owned
#: display state rather than a record field — same mechanism the business
#: pack's computed hours line uses.
SYSTEMS_TABLE_PATH = "/ui/systems/table"

_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The system's name as the user said it, e.g. 'SAP ECC'.",
            },
            "function": {
                "type": "string",
                "description": "What it is used for. Empty string if not stated.",
            },
            "hosting_location": {
                "type": "string",
                "description": (
                    "Where it runs: an on-premises data centre, a named cloud "
                    "region, or public SaaS. Empty string if not stated."
                ),
            },
            "interface": {
                "type": "string",
                "description": (
                    "How it is reached programmatically: native connector, "
                    "REST API, JDBC/SQL, file drop. Empty string if not stated."
                ),
            },
            "data_format": {
                "type": "string",
                "description": "Tables, documents, objects. Empty string if not stated.",
            },
            "schema_status": {
                "type": "string",
                "description": (
                    "Whether schemas or API specs exist. Use the word "
                    "'undocumented' when the user says they do not. Empty "
                    "string if not stated."
                ),
            },
            "system_owner": {
                "type": "string",
                "description": "Named DBA or owner. Empty string if not stated.",
            },
            "evidence": {
                "type": "string",
                "description": (
                    "The user's own words naming this system, quoted exactly "
                    "from the transcript. Never your own paraphrase."
                ),
            },
        },
        "required": ["name", "evidence"],
    },
}

_INSTRUCTION = """You are reading a transcript of a technical architecture \
review. Your only job is to list the backend systems the user has **already \
named**, with whatever technical detail they gave about each.

For every system, fill only the attributes the user actually stated. Leave \
every other attribute as an empty string.

Rules, in order of importance:

1. **Report only systems the user named.** Do not add systems that "usually" \
accompany the ones mentioned. A user who says "SAP" has not said "Oracle".
2. **Quote your evidence exactly.** Copy the user's own words naming the \
system into `evidence`. Evidence that does not appear in the transcript is \
discarded.
3. **Never infer an attribute.** If the user names SAP but does not say where \
it runs, `hosting_location` is an empty string. Do not write "likely \
on-premises". An unknown that looks like a fact is the single most damaging \
thing this review can produce.
4. **Returning an empty list is a correct answer** and is right whenever the \
conversation has not reached systems yet.
5. **One entry per system**, not per mention. If SAP comes up three times, \
merge what was said into one entry."""


def extract_systems(
    conversation: str, client: Any, existing: list[SystemEntry] | None = None
) -> list[SystemEntry]:
    """Builds the systems matrix from the transcript.

    Merges onto `existing` rather than replacing it: a later turn that adds the
    DBA's name for a system named three turns ago must not wipe the hosting
    location captured in between.

    Never raises. A failed extraction costs the turn its table, not the
    conversation.
    """
    try:
        raw = client.propose(
            instruction=_INSTRUCTION, schema=_SCHEMA, conversation=conversation
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("Systems extraction failed, continuing without it: %s", exc)
        return list(existing or [])

    return merge_systems(existing or [], _parse(raw, conversation))


def _parse(raw: Any, conversation: str) -> list[SystemEntry]:
    """Turns a model response into entries, discarding anything unsupported."""
    if not isinstance(raw, list):
        log.warning("Systems extraction returned %s, not a list", type(raw).__name__)
        return []

    haystack = conversation.lower()
    entries: list[SystemEntry] = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()
        if not name:
            continue

        # Same rule as the field extractor: unquotable evidence means the model
        # is describing its own reasoning rather than the user's words.
        evidence = str(item.get("evidence") or "").strip()
        if not evidence or evidence.lower() not in haystack:
            log.info("Discarded system %r: evidence not found in transcript", name)
            continue

        entries.append(
            SystemEntry(
                name=name,
                function=_clean(item.get("function")),
                hosting_location=_clean(item.get("hosting_location")),
                interface=_clean(item.get("interface")),
                data_format=_clean(item.get("data_format")),
                schema_status=_clean(item.get("schema_status")),
                system_owner=_clean(item.get("system_owner")),
            )
        )

    return entries


def _clean(value: Any) -> str | None:
    """Empty strings become None, so "not stated" is one value and not two."""
    text = str(value or "").strip()
    return text or None


def merge_systems(
    existing: list[SystemEntry], incoming: list[SystemEntry]
) -> list[SystemEntry]:
    """Combines two passes over the same matrix, keyed on the system name.

    A later turn fills gaps and overwrites stated attributes, but never blanks
    a known one — the extractor returns empty strings for anything the *current*
    excerpt did not mention, and treating those as deletions would lose a fact
    every time the reviewer changed subject.
    """
    merged: dict[str, SystemEntry] = {s.name.strip().lower(): s for s in existing}

    for entry in incoming:
        key = entry.name.strip().lower()
        prior = merged.get(key)
        if prior is None:
            merged[key] = entry
            continue

        merged[key] = SystemEntry(
            name=prior.name,
            function=entry.function or prior.function,
            hosting_location=entry.hosting_location or prior.hosting_location,
            interface=entry.interface or prior.interface,
            data_format=entry.data_format or prior.data_format,
            schema_status=entry.schema_status or prior.schema_status,
            system_owner=entry.system_owner or prior.system_owner,
        )

    return list(merged.values())


def render_systems_table(record: UseCaseRecord) -> str:
    """The matrix as markdown, for the locked `/ui` field in stage 1.

    Falls back to Phase 1's flat source list when the review has not itemised
    anything yet, so the reviewer always sees the inventory that does exist
    rather than an empty box.
    """
    systems = record.technical.systems

    if not systems:
        sources = [s for s in record.technical.data_sources if s != "other"]
        if record.technical.other_data_sources:
            sources.append(record.technical.other_data_sources)
        if sources:
            listed = ", ".join(sources)
            return (
                f"From the business intake: {listed}. "
                f"None verified technically yet — tell me where each one runs "
                f"and how we reach it."
            )
        return "No systems recorded yet. Name the backends this needs and I'll build the matrix."

    lines = [
        "| System | Hosting | Interface | Schema | Owner |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in systems:
        lines.append(
            f"| {s.name} | {s.hosting_location or '⚠️'} | {s.interface or '⚠️'} "
            f"| {s.schema_status or '⚠️'} | {s.system_owner or '⚠️'} |"
        )

    gaps = sum(
        1
        for s in systems
        for v in (s.hosting_location, s.interface, s.schema_status, s.system_owner)
        if not v
    )
    if gaps:
        lines.append("")
        lines.append(f"⚠️ marks {gaps} attribute(s) still to confirm.")

    return "\n".join(lines)
