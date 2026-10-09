"""The one data-classification vocabulary shared by both packs and scoring.

Phase 1 (business.yaml) and Phase 2 (tech.yaml) both write
`/uc/technical/security/data_classification`, and subcriterion 3.4 and the
capability tier both read it. When the two packs used different words, the
most sensitive Phase 1 answer ("restricted") scored as "Unclassified" in 3.4,
and the tech pack's "regulated" never triggered the Level 6 path. So the
values live here, once. Packs may label them however a tenant likes; the
values they store must come from `VOCABULARY` (enforced by tests/test_packs.py).

Records saved before the vocabularies were unified may hold a legacy value.
`normalise_classification` maps those onto the canonical set, so every reader
goes through it rather than comparing raw strings.
"""

from __future__ import annotations

#: Labelled with a sensitivity tier. 3.4 passes these (with residency known).
CLASSIFIED = ("public", "internal", "confidential", "regulated")
#: The most sensitive tier: PII, HIPAA, PCI or tenant-"restricted" data.
REGULATED = "regulated"
#: Answered, but the data mixes tiers; a review is scheduled.
MIXED = "mixed"
#: Answered: the data has never been classified (potential untagged PII).
UNCLASSIFIED = "unclassified"
#: Not an answer: "Not sure yet" / "Not yet confirmed".
UNKNOWN = "unknown"

VOCABULARY = (*CLASSIFIED, MIXED, UNCLASSIFIED, UNKNOWN)

#: Legacy or alternative spellings -> canonical value.
_ALIASES = {
    # business.yaml stored "Restricted or regulated" as `restricted`.
    "restricted": REGULATED,
    "not_sure": UNKNOWN,
    "not sure": UNKNOWN,
    "not yet confirmed": UNKNOWN,
}

#: Business-audience labels.
LABELS = {
    "public": "Public",
    "internal": "Internal",
    "confidential": "Confidential",
    REGULATED: "Restricted or regulated",
    MIXED: "Mixed or unclear",
    UNCLASSIFIED: "Unclassified",
}


def normalise_classification(value: str | None) -> str | None:
    """Canonical classification for a stored value; None when empty.

    Unrecognised values are returned lower-cased rather than dropped, so a
    scorer can still say what it did not understand.
    """
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    return _ALIASES.get(cleaned, cleaned)
