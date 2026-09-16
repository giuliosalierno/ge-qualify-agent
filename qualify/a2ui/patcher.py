"""Reading field values out of what the user said.

The interview is conversational, but the record is structured. Something has
to turn "about a dozen handlers, takes them twenty minutes a pop" into
`user_count = 12` and `baseline_minutes_per_task = 20`. That is this module.

**It is built to under-fill.** An empty field costs one more question. A
confidently wrong field costs the credibility of the whole brief, because the
user stops trusting the numbers and starts checking all of them. So every
rule here resolves ties towards dropping the value.

The strongest of those rules is **evidence**. The model must return the
user's own words alongside each value, and a draft whose evidence does not
appear in the conversation is discarded. A model that invents a value must
also invent a quote to justify it, and inventing a quote that survives a
substring check against the real transcript is much harder than inventing a
plausible number.

Nothing here calls a model directly. The extraction call sits behind
:class:`ExtractionClient`, which keeps `qualify` free of an LLM dependency and
lets every guard below be tested with a stub. The real adapter is at the
bottom of the file and imports `google.genai` lazily.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from qualify.a2ui.provenance import draft
from qualify.packs.loader import Pack, Stage
from qualify.schema.coerce import CoercionError, coerce_value
from qualify.schema.use_case_record import OwnershipError, UseCaseRecord

log = logging.getLogger(__name__)

#: Gemini 3.8 Flash. Extraction is a short, schema-constrained task run on every
#: turn, so latency and cost matter more than reasoning depth here.
DEFAULT_MODEL = "gemini-3.8-flash"

#: Trimmed from both ends of an evidence quote before matching. A model that
#: quotes `"twelve handlers."` when the user wrote `"twelve handlers"` is
#: quoting correctly; punishing it for a full stop would drop a good value.
_EDGE = " \t\n\r.,;:!?\"'`()[]"


@dataclass(frozen=True)
class FieldDraft:
    """One value the extractor believes the user gave.

    `evidence` is not decoration. It is the check — see the module docstring.
    """

    path: str
    value: Any
    evidence: str


@dataclass
class ExtractionResult:
    """Accepted drafts, plus what was thrown away and why.

    The rejections are kept rather than silently dropped. A model quietly
    losing every value is indistinguishable from a quiet user unless someone
    can see the discard reasons, and that is exactly the failure that would
    otherwise be found in production.
    """

    drafts: list[FieldDraft]
    rejected: list[tuple[str, str]]

    def paths(self) -> list[str]:
        return [d.path for d in self.drafts]


class ExtractionClient(Protocol):
    """The seam where a model plugs in.

    Implementations own the call and return raw dicts. Everything about
    whether a dict is trustworthy is decided here, not there, so a different
    backend cannot weaken the guards by accident.
    """

    def propose(
        self, *, instruction: str, schema: dict[str, Any], conversation: str
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Prompt and schema
# ---------------------------------------------------------------------------


def writable_fields(stage: Stage) -> list:
    """The fields of `stage` the extractor may propose values for."""
    return [f for f in stage.fields if not f.readonly]


def build_schema(stage: Stage, pack: Pack) -> dict[str, Any]:
    """A structured-output schema pinned to this stage's fields.

    Constraining `path` to an enum of real paths and option values to the
    declared vocabulary stops a whole class of nonsense at the source. The
    filter re-checks both anyway — a schema is a request, not a guarantee,
    and structured output has been known to drift.
    """
    fields = writable_fields(stage)
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": [f.path for f in fields],
                },
                "value": {
                    "type": "string",
                    "description": (
                        "The value, as a plain string. Numbers as digits "
                        "with no units. Choices as the option value."
                    ),
                },
                "evidence": {
                    "type": "string",
                    "description": (
                        "The user's own words that give this value, quoted "
                        "exactly from the conversation. Never your own "
                        "paraphrase."
                    ),
                },
            },
            "required": ["path", "value", "evidence"],
        },
    }


def build_field_menu(stage: Stage, pack: Pack) -> str:
    """Describes the stage's fields to the model, one per line."""
    lines = []
    for spec in writable_fields(stage):
        parts = [f"- {spec.path} — {spec.label}"]
        if spec.help:
            parts.append(f"({spec.help})")

        options = pack.options_for(spec)
        if options:
            allowed = ", ".join(f"{o.value}" for o in options)
            parts.append(f"[choose from: {allowed}]")
        elif spec.variant == "number":
            parts.append("[a number, digits only]")
        elif spec.resolved is not None and spec.resolved.is_bool:
            parts.append("[true or false]")

        lines.append(" ".join(parts))
    return "\n".join(lines)


def build_instruction(stage: Stage, pack: Pack) -> str:
    """The extraction prompt.

    Deliberately not the agent's persona prompt. This one has a single job
    and an explicit licence to return nothing, which is hard to combine with
    a prompt that also has to hold a warm conversation.
    """
    return f"""You are reading a transcript of an interview about a business \
process. Your only job is to spot values the user has **already stated** for \
the fields below, and report them.

Fields you may fill for the current stage ({stage.label}):

{build_field_menu(stage, pack)}

Rules, in order of importance:

1. **Report only what the user said.** Do not infer, deduce, estimate or \
complete. If a user describes a slow process but never says how slow, there \
is no value for the duration field.
2. **Quote your evidence exactly.** For every value, copy the user's own \
words from the transcript into `evidence`. Never paraphrase and never quote \
yourself — evidence that does not appear verbatim in the transcript is \
discarded.
3. **Returning an empty list is a correct answer** and often the right one. \
A missing value costs one more question; a wrong one costs the user's trust \
in every other number.
4. **Do not repeat the question back.** If the assistant asked "how many \
people?" and the user has not answered, there is no value.
5. **Numbers as digits, no units.** "about a dozen" is 12. "twenty minutes" \
is 20. "a couple of hours" is not a number the user gave precisely — skip it.
6. **Choices must use the exact option value** listed above, never the label \
and never a new one.

Report nothing for any field the user has not addressed."""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_drafts(
    stage: Stage,
    pack: Pack,
    conversation: str,
    client: ExtractionClient,
) -> ExtractionResult:
    """Asks the model for values, then refuses most of what could go wrong.

    Never raises on a bad model response. A failed extraction should cost the
    turn its auto-fill, not the conversation.
    """
    instruction = build_instruction(stage, pack)
    schema = build_schema(stage, pack)

    try:
        raw = client.propose(
            instruction=instruction, schema=schema, conversation=conversation
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("Extraction call failed, continuing without it: %s", exc)
        return ExtractionResult(drafts=[], rejected=[("*", str(exc))])

    return _filter(raw, stage, pack, conversation)


def _filter(
    raw: Any, stage: Stage, pack: Pack, conversation: str
) -> ExtractionResult:
    """Every reason a proposed value does not make it into the record."""
    drafts: list[FieldDraft] = []
    rejected: list[tuple[str, str]] = []

    if not isinstance(raw, list):
        return ExtractionResult([], [("*", f"expected a list, got {type(raw).__name__}")])

    by_path = {f.path: f for f in writable_fields(stage)}
    haystack = _normalise(conversation)
    seen: set[str] = set()

    for item in raw:
        if not isinstance(item, dict):
            rejected.append(("*", f"not an object: {item!r}"))
            continue

        path = item.get("path")
        if not isinstance(path, str):
            rejected.append(("*", "missing path"))
            continue

        spec = by_path.get(path)
        if spec is None:
            # Either a hallucinated path or a field from another stage. Both
            # are refusals: confirmation should mean "the user just saw this".
            rejected.append((path, "not a writable field of this stage"))
            continue

        if path in seen:
            rejected.append((path, "duplicate; keeping the first"))
            continue

        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            rejected.append((path, "no evidence quoted"))
            continue

        if not _quoted_from(evidence, haystack):
            # The strongest guard in the module. See the module docstring.
            rejected.append((path, f"evidence not found in conversation: {evidence!r}"))
            continue

        value = item.get("value")
        if spec.resolved is None:
            rejected.append((path, "field has no resolved type"))
            continue

        allowed = {o.value for o in pack.options_for(spec)}
        if allowed and not _within_options(value, allowed):
            rejected.append((path, f"{value!r} is not one of the declared options"))
            continue

        try:
            coerced = coerce_value(spec.resolved, value)
        except CoercionError as exc:
            rejected.append((path, str(exc)))
            continue

        if coerced is None or coerced == [] or coerced == "":
            # The model found the field but not a value in it. Nothing to say.
            rejected.append((path, "coerced to empty"))
            continue

        seen.add(path)
        drafts.append(FieldDraft(path=path, value=coerced, evidence=evidence))

    for path, reason in rejected:
        log.info("Extractor dropped %s: %s", path, reason)

    return ExtractionResult(drafts=drafts, rejected=rejected)


def _within_options(value: Any, allowed: set[str]) -> bool:
    """Checks a proposed choice against the declared vocabulary.

    Handles the list case so a multi-select cannot smuggle one invented
    option in beside three real ones.
    """
    if isinstance(value, list):
        return bool(value) and all(str(v) in allowed for v in value)
    return str(value) in allowed


def _normalise(text: str) -> str:
    """Casefolds and collapses whitespace, so quoting is not a typing test."""
    return re.sub(r"\s+", " ", text).casefold().strip()


def _quoted_from(evidence: str, haystack: str) -> bool:
    """True when the evidence really appears in the conversation.

    Compares on normalised text and ignores edge punctuation, because the
    intent is to catch invention, not to penalise a trailing full stop.
    """
    needle = _normalise(evidence).strip(_EDGE)
    if not needle:
        return False
    return needle in haystack


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def apply_drafts(
    record: UseCaseRecord, drafts: list[FieldDraft]
) -> list[FieldDraft]:
    """Writes accepted drafts into the record, returning the ones that landed.

    Routes through `provenance.draft`, so a field the user has already
    confirmed is never overwritten. That matters more than it sounds: the
    extractor re-reads the whole conversation every turn, so without the
    guard it would keep re-deriving the value a user corrected and every
    manual fix would survive exactly one turn.
    """
    applied: list[FieldDraft] = []
    for item in drafts:
        try:
            if draft(record, item.path, item.value):
                applied.append(item)
        except (CoercionError, OwnershipError) as exc:
            # Already filtered for both, so reaching here means a pack or a
            # schema changed underneath us. Worth a warning, not a crash.
            log.warning("Could not apply draft %s: %s", item.path, exc)
    return applied


# ---------------------------------------------------------------------------
# The real client
# ---------------------------------------------------------------------------


class GeminiExtractionClient:
    """Calls Gemini with structured output.

    `google.genai` is imported inside the constructor, not at module scope,
    so importing `qualify` needs no SDK and no credentials. The test suite
    depends on that: every guard above is exercised with a stub.
    """

    def __init__(self, model: str = DEFAULT_MODEL, client: Any = None) -> None:
        self.model = model
        if client is not None:
            self._client = client
            return
        import os  # noqa: PLC0415
        from google import genai  # noqa: PLC0415 - deliberate, see docstring

        # Gemini 3 preview models require the 'global' location on Vertex AI.
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        if "gemini-3" in self.model and location != "global":
            location = "global"

        if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE":
            self._client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                location=location,
            )
        else:
            self._client = genai.Client()

    def propose(
        self, *, instruction: str, schema: dict[str, Any], conversation: str
    ) -> list[dict[str, Any]]:
        import json  # noqa: PLC0415

        config = {
            "system_instruction": instruction,
            "response_mime_type": "application/json",
            "response_schema": schema,
            # Extraction is a copying task. Sampling variety here buys
            # nothing and costs reproducibility between identical turns.
            "temperature": 0.0,
        }
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=conversation,
                config=config,
            )
        except Exception as exc:
            if "404" in str(exc) and self.model != "gemini-3-flash-preview":
                log.warning("Model %s returned 404, falling back to gemini-3-flash-preview", self.model)
                response = self._client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=conversation,
                    config=config,
                )
            else:
                raise

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("Extractor returned invalid JSON: %s", exc)
            return []
        return parsed if isinstance(parsed, list) else []
