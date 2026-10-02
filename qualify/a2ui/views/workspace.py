"""The opportunity workspace: one side panel per opportunity, for either pack.

The chat collects answers; the workspace shows where the opportunity stands.
It never asks the user to type. Every button does one predictable thing:

* *Revise* / *Fill now* / *Show the form* post that stage's form in the chat
  (``revise_stage``, the same event the stage cards already raise).
* Documents expand in place, so reading one costs no round trip.
* *Open in <storage>* links go to the real file when storage is connected.

Tabs:

* **Progress**: stages with ✅ confirmed / ⚠️ skipped / ✏️ current / ⚪ not
  started, the answers captured so far, and what is still missing before the
  deliverable can be submitted.
* **Checklist** (technical review only): the 22 checks from
  ``scoring.technical``, blockers first, grouped by dimension.
* **Documents**: the business brief and the technical dossier, previewed from
  the live record with the same renderers that write the files, so the panel
  and the files never disagree.

Driven entirely by the pack, so a new pack gets a workspace with no code here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from qualify.a2ui.actions import REVISE_STAGE
from qualify.a2ui.views import components as ui
from qualify.a2ui.views.events import OPEN_WORKSPACE, START_TECH_REVIEW
from qualify.packs.loader import FieldSpec, Pack, Stage
from qualify.schema.coerce import get_by_path
from qualify.schema.use_case_record import UseCaseRecord

#: Tabs the caller can bring to the front.
FOCUS_PROGRESS = "progress"
FOCUS_CHECKLIST = "checklist"
FOCUS_DOCUMENTS = "documents"

_STATE_ICONS = {
    "confirmed": "✅",
    "skipped": "⚠️",
    "current": "✏️",
    "todo": "⚪",
}

#: Long free text is cut in the progress list; the full answer is in the brief.
_MAX_ANSWER_CHARS = 160


# ---------------------------------------------------------------------------
# Stage status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageStatus:
    idx: int
    stage: Stage
    state: str  # confirmed | skipped | current | todo
    answers: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def icon(self) -> str:
        return _STATE_ICONS[self.state]


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def _option_label(pack: Pack, spec: FieldSpec, value: Any) -> str:
    if spec.options_ref:
        for opt in pack.option_sets.get(spec.options_ref, []):
            if opt.value == value:
                return opt.label
    return str(value)


def format_answer(pack: Pack, spec: FieldSpec, value: Any) -> str:
    """A captured value as one short, human line."""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_option_label(pack, spec, v) for v in value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    text = _option_label(pack, spec, value).strip().replace("\n", " ")
    if len(text) > _MAX_ANSWER_CHARS:
        text = text[: _MAX_ANSWER_CHARS - 1].rstrip() + "…"
    return text


def stage_statuses(
    pack: Pack,
    record: UseCaseRecord,
    *,
    committed: set[int],
    skipped: set[int],
    active_stage: int,
    complete: bool,
) -> list[StageStatus]:
    out: list[StageStatus] = []
    for idx, stage in enumerate(pack.stages):
        if idx in skipped:
            state = "skipped"
        elif idx in committed:
            state = "confirmed"
        elif idx == active_stage and not complete:
            state = "current"
        else:
            state = "todo"
        answers: list[tuple[str, str]] = []
        missing: list[str] = []
        for spec in stage.fields:
            if spec.readonly or not spec.path.startswith("/uc/"):
                continue
            value = get_by_path(record, spec.path)
            if _is_blank(value):
                if spec.required:
                    missing.append(spec.label)
                continue
            answers.append((spec.label, format_answer(pack, spec, value)))
        # The systems matrix is a list of objects shown as a locked table, not
        # a form field, so it is summarised here by name.
        if stage.id == "systems" and record.technical.systems:
            names = ", ".join(sys_.name for sys_ in record.technical.systems)
            answers.insert(0, ("Systems itemised", names))
        out.append(StageStatus(idx, stage, state, answers, missing))
    return out


# ---------------------------------------------------------------------------
# Progress tab
# ---------------------------------------------------------------------------


def _progress_bar(done: int, total: int) -> str:
    pct = round(100 * done / total) if total else 0
    return f"{'▰' * done}{'▱' * (total - done)}  {pct}%"


def _stage_description(st: StageStatus) -> str:
    n = len(st.answers)
    answers = f"{n} answer{'s' if n != 1 else ''}" if n else "no answers yet"
    if st.state == "confirmed":
        return f"Confirmed · {answers}" + (f" · {len(st.missing)} missing" if st.missing else "")
    if st.state == "skipped":
        return "Skipped · needs follow-up"
    if st.state == "current":
        return f"In progress · {answers}" + (
            f" · {len(st.missing)} required missing" if st.missing else ""
        )
    return "Not started"


def _stage_body(st: StageStatus) -> str:
    lines = [f"- **{label}:** {value}" for label, value in st.answers]
    lines += [f"- **{label}:** _missing (required)_" for label in st.missing]
    if not lines:
        return "_Nothing captured yet._"
    return "\n".join(lines)


def _stage_button(
    st: StageStatus, pack: Pack, cid: str, todo_after: str | None
) -> tuple[list[str], list[ui.Component]]:
    ctx = {"stage": st.stage.id, "pack": pack.pack}
    if st.state == "confirmed":
        return [cid], ui.event_button(cid, "Revise answers", REVISE_STAGE, ctx)
    if st.state == "skipped":
        return [cid], ui.event_button(cid, "Fill now", REVISE_STAGE, ctx, primary=True)
    if st.state == "current":
        return [cid], ui.event_button(cid, "Show the form in chat", REVISE_STAGE, ctx)
    note = f"Opens after {todo_after}." if todo_after else "Not started yet."
    return [cid], [ui.text(cid, note, "caption")]


def _progress_tab(pack: Pack, statuses: list[StageStatus], complete: bool) -> list[ui.Component]:
    total = len(statuses)
    done = sum(1 for s in statuses if s.state in ("confirmed", "skipped"))
    n_skipped = sum(1 for s in statuses if s.state == "skipped")

    headline = (
        f"All {total} stages done" if complete else f"{done} of {total} stages done"
    )
    bar = _progress_bar(done, total) + (f" · {n_skipped} skipped" if n_skipped else "")

    # Stages not reached yet are summarised in one line: listing every field
    # of every future stage on the first turn reads as a wall of failures.
    seen = [s for s in statuses if s.state != "todo"]
    gaps = [
        f"- **{s.idx + 1}. {s.stage.label}:** {', '.join(s.missing)}"
        for s in seen
        if s.missing
    ]
    gaps += [
        f"- **{s.idx + 1}. {s.stage.label}:** skipped, not yet confirmed"
        for s in seen
        if s.state == "skipped" and not s.missing
    ]
    n_todo = len(statuses) - len(seen)
    if n_todo:
        gaps.append(f"- {n_todo} more stage{'s' if n_todo != 1 else ''} not started yet")
    if gaps:
        title = "⚠️ **Still open**" if complete else "⚠️ **Missing before you submit**"
        missing_text = title + "\n\n" + "\n".join(gaps)
    else:
        missing_text = "✅ Every required answer is captured."

    ids = ["ws-pg-head", "ws-pg-bar", "ws-pg-missing", "ws-pg-rule"]
    nodes: list[ui.Component] = [
        ui.text("ws-pg-head", headline, "h4"),
        ui.text("ws-pg-bar", bar, "caption"),
        ui.text("ws-pg-missing", missing_text, "body"),
        ui.divider("ws-pg-rule"),
    ]

    first_open = next((s.idx for s in statuses if s.state == "skipped"), None)
    for st in statuses:
        sid = f"ws-st-{st.stage.id}"
        body_id, btn_id = f"{sid}-body", f"{sid}-btn"
        prev = statuses[st.idx - 1] if st.idx > 0 else None
        todo_after = f"stage {prev.idx + 1}" if prev is not None else None
        btn_ids, btn_nodes = _stage_button(st, pack, btn_id, todo_after)
        expanded = st.state == "current" or (complete and st.idx == first_open)
        nodes.append(
            ui.panel(
                sid,
                f"{st.icon} {st.idx + 1}. {st.stage.label}",
                [body_id, *btn_ids],
                description=_stage_description(st),
                expanded=expanded,
            )
        )
        nodes.append(ui.text(body_id, _stage_body(st), "body"))
        nodes += btn_nodes
        ids.append(sid)

    nodes.insert(0, ui.column("ws-progress", ids))
    return nodes


# ---------------------------------------------------------------------------
# Checklist tab (technical review)
# ---------------------------------------------------------------------------

_VERDICT_ICONS = {2: "✅", 1: "⚠️", 0: "❌"}


def _checklist_tab(record: UseCaseRecord) -> list[ui.Component]:
    from qualify.export.dossier import _checklist  # noqa: PLC0415
    from qualify.scoring.technical import score_technical  # noqa: PLC0415

    score = score_technical(record)
    key2 = "✅ approved" if score.key2_ready else "⏳ pending"

    if score.blockers:
        block = "⛔ **Blockers: this cannot proceed as scoped**\n\n" + "\n".join(
            f"- **{b.id} {b.label}:** {b.rationale}" for b in score.blockers
        )
    elif score.unconfirmed_blockers:
        block = "❓ **Deciding questions not asked yet** (Key 2 needs both)\n\n" + "\n".join(
            f"- **{b.id} {b.label}**" for b in score.unconfirmed_blockers
        )
    else:
        block = "✅ No blockers found."

    ids = ["ws-ck-head", "ws-ck-meta", "ws-ck-block", "ws-ck-rule"]
    nodes: list[ui.Component] = [
        ui.text(
            "ws-ck-head",
            f"Technical readiness {score.readiness_pct}% ({score.earned}/{score.maximum})",
            "h4",
        ),
        ui.text(
            "ws-ck-meta",
            f"Feasibility profile: {score.feasibility_profile} · Key 2: {key2} · "
            f"{len(score.unanswered)} of {len(score.subscores)} not discussed yet",
            "caption",
        ),
        ui.text("ws-ck-block", block, "body"),
        ui.divider("ws-ck-rule"),
    ]

    for n, (dimension, rows) in enumerate(score.by_dimension().items(), start=1):
        pid = f"ws-ck-d{n}"
        subtotal = sum(r.points for r in rows)
        fails = sum(1 for r in rows if r.answered and r.points == 0)
        warns = sum(1 for r in rows if r.points == 1)
        open_ = sum(1 for r in rows if not r.answered)
        parts = [f"{fails} fail"] if fails else []
        parts += [f"{warns} warn"] if warns else []
        parts += [f"{open_} not discussed"] if open_ else []
        lines = []
        for r in rows:
            if r.answered:
                lines.append(f"- {_VERDICT_ICONS[r.points]} **{r.id} {r.label}** — {r.rationale}")
            else:
                lines.append(f"- ⚪ **{r.id} {r.label}** — _not yet discussed_")
        nodes.append(
            ui.panel(
                pid,
                f"{dimension} — {subtotal}/{len(rows) * 2}",
                [f"{pid}-body"],
                description=" · ".join(parts) or "all clear",
                expanded=fails > 0,
            )
        )
        nodes.append(ui.text(f"{pid}-body", "\n".join(lines), "body"))
        ids.append(pid)

    items = _checklist(record, score)
    done = sum(1 for _, ok, _ in items if ok)
    lines = [
        f"- {'☑' if ok else '☐'} {label}" + ("" if ok else f" — blocked on {sub}")
        for label, ok, sub in items
    ]
    nodes.append(
        ui.panel(
            "ws-ck-access",
            "Sprint #1 access checklist",
            ["ws-ck-access-body"],
            description=f"{done} of {len(items)} cleared",
        )
    )
    nodes.append(ui.text("ws-ck-access-body", "\n".join(lines), "body"))
    ids.append("ws-ck-access")

    nodes.insert(0, ui.column("ws-checklist", ids))
    return nodes


# ---------------------------------------------------------------------------
# Documents tab
# ---------------------------------------------------------------------------

_ALERTS = {
    "[!CAUTION]": "⛔",
    "[!WARNING]": "⚠️",
    "[!IMPORTANT]": "❗",
    "[!NOTE]": "ℹ️",
    "[!TIP]": "💡",
}


def _clean_markdown(body: str) -> str:
    """Strips what a plain ``Text`` is not promised to render.

    Blockquotes and GitHub alerts become plain lines with an icon; rules
    disappear (the section boundaries already separate content). Sub-headings
    (``###``…) become bold lines: the section title above them is an ``h5``,
    and a markdown heading inside the body would render larger than it.
    """
    out: list[str] = []
    prev_quote = False
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.strip() == "---":
            continue
        heading = re.match(r"#{1,6}\s+(.+)", line)
        if heading:
            line = f"**{heading.group(1).strip()}**"
        is_quote = line.startswith(">")
        if is_quote:
            line = line.lstrip(">").strip()
            for marker, icon in _ALERTS.items():
                if line.startswith(marker):
                    line = icon
                    break
            # Without "> " the trailing-space hard break is gone, so back-to-
            # back quote lines would merge into one paragraph.
            if prev_quote and out and out[-1] not in _ALERTS.values():
                out.append("")
        prev_quote = is_quote
        out.append(line)
    # Join an alert icon with the line that follows it.
    merged: list[str] = []
    for line in out:
        if merged and merged[-1] in _ALERTS.values():
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    return "\n".join(merged).strip()


def markdown_sections(md: str) -> list[tuple[str, str]]:
    """Splits a deliverable on ``## `` headings: ``[(title, body), ...]``.

    The ``# `` document title is dropped (the panel already names the
    document); anything before the first ``## `` becomes "Overview".
    """
    sections: list[tuple[str, list[str]]] = [("Overview", [])]
    for line in md.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        else:
            sections[-1][1].append(line)
    out = []
    for title, lines in sections:
        body = _clean_markdown("\n".join(lines))
        if body:
            out.append((title, body))
    return out


@dataclass(frozen=True)
class _Doc:
    key: str  # "brief" | "dossier"
    link_key: str  # pack whose sync wrote it
    title: str
    status: str
    markdown: str | None


def _documents(
    pack: Pack, record: UseCaseRecord, skipped: set[int], complete: bool
) -> list[_Doc]:
    from qualify.export.brief import render_business_brief  # noqa: PLC0415
    from qualify.export.dossier import render_technical_dossier  # noqa: PLC0415

    if pack.pack == "tech":
        return [
            _Doc(
                "brief",
                "business",
                "Business Value Brief",
                "Final · the input to this review",
                render_business_brief(record),
            ),
            _Doc(
                "dossier",
                "tech",
                "Technical Architecture Dossier",
                "Final" if complete else "Working draft · updates as you answer",
                render_technical_dossier(record, skipped_stages=skipped),
            ),
        ]
    return [
        _Doc(
            "brief",
            "business",
            "Business Value Brief",
            "Final" if complete else "Draft · updates as you answer",
            render_business_brief(record, skipped_stages=skipped),
        ),
        _Doc(
            "dossier",
            "tech",
            "Technical Architecture Dossier",
            "Not started · follows the business intake",
            None,
        ),
    ]


def _documents_tab(
    pack: Pack,
    record: UseCaseRecord,
    *,
    skipped: set[int],
    complete: bool,
    links: dict[str, str],
    storage_label: str | None,
    expand: str | None,
) -> list[ui.Component]:
    ids: list[str] = []
    nodes: list[ui.Component] = []
    where = storage_label or "storage"

    if links.get("folder"):
        ids.append("ws-doc-folder")
        nodes.append(
            ui.text(
                "ws-doc-folder",
                f"📁 [Open the opportunity folder in {where}]({links['folder']})",
                "body",
            )
        )
    elif storage_label is None:
        ids.append("ws-doc-note")
        nodes.append(
            ui.text(
                "ws-doc-note",
                "Previews are generated from the live record. Document storage is "
                "off in this deployment, so there are no files to open.",
                "caption",
            )
        )
    else:
        ids.append("ws-doc-note")
        nodes.append(
            ui.text(
                "ws-doc-note",
                f"Files are written to {where} when a stage set is submitted. "
                "Until then, these previews are generated from the live record.",
                "caption",
            )
        )

    for doc in _documents(pack, record, skipped, complete):
        pid = f"ws-doc-{doc.key}"
        children: list[str] = []
        if links.get(doc.link_key):
            children.append(f"{pid}-link")
            nodes.append(
                ui.text(f"{pid}-link", f"🔗 [Open in {where}]({links[doc.link_key]})", "body")
            )
        if doc.markdown is None:
            children.append(f"{pid}-empty")
            nodes.append(
                ui.text(f"{pid}-empty", "Starts when the technical review begins.", "caption")
            )
            if complete and pack.pack == "business":
                children.append(f"{pid}-start")
                nodes += ui.event_button(
                    f"{pid}-start",
                    "Start technical review",
                    START_TECH_REVIEW,
                    {"recordId": record.meta.record_id},
                    primary=True,
                )
        else:
            for i, (title, body) in enumerate(markdown_sections(doc.markdown)):
                if doc.key == "dossier" and "Audit Matrix" in title:
                    body = "The full 22-check matrix is in the **Checklist** tab."
                # A divider and an h4 title per section; with h5 and no gap
                # the sections read as one block.
                if i:
                    children.append(f"{pid}-s{i}-d")
                    nodes.append(ui.divider(f"{pid}-s{i}-d"))
                children += [f"{pid}-s{i}-h", f"{pid}-s{i}-b"]
                nodes += [
                    ui.text(f"{pid}-s{i}-h", title, "h4"),
                    ui.text(f"{pid}-s{i}-b", body, "body"),
                ]
        nodes.append(
            ui.panel(
                pid,
                f"📄 {doc.title}",
                children,
                description=doc.status,
                expanded=expand == doc.key,
            )
        )
        ids.append(pid)

    nodes.insert(0, ui.column("ws-documents", ids))
    return nodes


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


def build_workspace_view(
    *,
    pack: Pack,
    record: UseCaseRecord,
    committed: set[int],
    skipped: set[int],
    active_stage: int,
    surface_id: str,
    complete: bool = False,
    links: dict[str, str] | None = None,
    storage_label: str | None = None,
    focus: str = FOCUS_PROGRESS,
    expand_document: str | None = None,
    auto_open: bool = True,
) -> list[dict[str, Any]]:
    """The full message sequence for the workspace side panel.

    ``focus`` puts that tab first; the Tabs component has no "selected"
    property, so order is the only way to choose what the user sees first.
    ``storage_label`` is the storage product name ("Google Drive"), or None
    when the deployment has no document storage.
    """
    statuses = stage_statuses(
        pack,
        record,
        committed=committed,
        skipped=skipped,
        active_stage=active_stage,
        complete=complete,
    )
    is_tech = pack.pack == "tech"

    nodes: list[ui.Component] = []
    tab_items: dict[str, tuple[str, str]] = {}

    nodes += _progress_tab(pack, statuses, complete)
    tab_items[FOCUS_PROGRESS] = ("Progress", "ws-progress")
    if is_tech:
        nodes += _checklist_tab(record)
        tab_items[FOCUS_CHECKLIST] = ("Checklist", "ws-checklist")
    nodes += _documents_tab(
        pack,
        record,
        skipped=skipped,
        complete=complete,
        links=links or {},
        storage_label=storage_label,
        expand=expand_document,
    )
    tab_items[FOCUS_DOCUMENTS] = ("Documents", "ws-documents")

    order = [focus] if focus in tab_items else []
    order += [k for k in tab_items if k not in order]

    name = record.meta.initiative_name or "Untitled use case"
    rid = record.meta.record_id
    done = sum(1 for s in statuses if s.state in ("confirmed", "skipped"))
    phase = "Technical review" if is_tech else "Business intake"
    status = "complete" if complete else f"{done}/{len(statuses)} stages done"

    nodes += [
        ui.text("ws-title", name, "h3"),
        ui.text("ws-meta", f"`{rid}` · {phase} · {status}", "caption"),
        ui.tabs("ws-tabs", [tab_items[k] for k in order]),
    ]
    root = ui.canvas_root(
        ["ws-title", "ws-meta", "ws-tabs"],
        title=f"Workspace — {name}",
        description=f"{rid} · {phase} · {status}",
        icon="folder_open",
        auto_open=auto_open,
    )
    return ui.surface(surface_id, [root, *nodes])


def add_open_workspace_button(
    messages: list[dict[str, Any]], record_id: str
) -> list[dict[str, Any]]:
    """Appends an *Open workspace* button to a stage card's root Column.

    Works on the message list ``compiler.build_surface`` returns, so the
    compiler (and its tests) stay unaware of the views layer.
    """
    out = [dict(m) for m in messages]
    for i, msg in enumerate(out):
        update = msg.get("updateComponents")
        if not update:
            continue
        comps = [dict(c) for c in update["components"]]
        root = next((c for c in comps if c.get("id") == "root"), None)
        if root is None or root.get("component") != "Column":
            return messages
        root["children"] = [*root["children"], "ws-open"]
        comps += ui.event_button(
            "ws-open", "🗂️ Open workspace", OPEN_WORKSPACE, {"recordId": record_id}
        )
        out[i] = {**msg, "updateComponents": {**update, "components": comps}}
        return out
    return messages
