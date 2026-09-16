"""Export renderers for the final deliverable of each pack.

One registry, so the turn engine and the SharePoint writer ask "what does this
pack produce?" instead of naming the business brief in nine different places.
Before this existed, `render_business_brief` was hardcoded at six call sites in
`turn.py` and `sharepoint.py`, and adding a second pack would have meant
finding all of them.
"""

from __future__ import annotations

from typing import Any, Callable

from qualify.export.brief import render_business_brief
from qualify.export.dossier import render_technical_dossier
from qualify.schema.use_case_record import UseCaseRecord

#: pack name -> the markdown artifact that pack produces at the end.
_RENDERERS: dict[str, Callable[..., str]] = {
    "business": render_business_brief,
    "tech": render_technical_dossier,
}

#: pack name -> the filename that artifact is written under.
#:
#: Separate files in one record folder, not one file overwritten. A technical
#: review must never destroy the business brief it was built from.
DELIVERABLE_FILENAMES: dict[str, str] = {
    "business": "Business_Value_Brief.md",
    "tech": "Technical_Architecture_Dossier.md",
}

DEFAULT_PACK = "business"


def render_deliverable(pack_name: str, record: UseCaseRecord, **kwargs: Any) -> str:
    """Renders the final artifact for a pack.

    Falls back to the business brief for an unknown pack rather than raising.
    A missing renderer is a programming error, but discovering it by losing a
    completed interview is the wrong way to find out.
    """
    renderer = _RENDERERS.get(pack_name, render_business_brief)
    return renderer(record, **kwargs)


def deliverable_filename(pack_name: str) -> str:
    """The filename this pack's deliverable is written under."""
    return DELIVERABLE_FILENAMES.get(pack_name, DELIVERABLE_FILENAMES[DEFAULT_PACK])


__all__ = [
    "DELIVERABLE_FILENAMES",
    "deliverable_filename",
    "render_business_brief",
    "render_deliverable",
    "render_technical_dossier",
]
