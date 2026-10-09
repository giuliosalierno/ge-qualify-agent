"""The welcome menu: one inline card offering the agent's three workflows.

Shown when a conversation opens with a greeting. A greeting tells us nothing
about which persona is speaking, so dropping a business owner, an architect
and a CoE lead alike into the Stage 1 intake form guessed wrong two times in
three. The card lets them pick instead.

Every button raises a server event that maps onto an existing chat command
(see ``turn._try_view_event``), so a click and a typed command stay identical.
Uses base ``Button`` via :func:`components.event_button`, the variant whose
events were watched reaching the agent in Gemini Enterprise.
"""

from __future__ import annotations

from typing import Any

from qualify.a2ui.compiler import ROOT_ID
from qualify.a2ui.views import components as ui
from qualify.a2ui.views.events import OPEN_PORTFOLIO, START_INTAKE, START_TECH_REVIEW

#: (id, title, description, button label, event)
_WORKFLOWS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "wm-intake",
        "**📋 Business Value Intake** — Phase 1, business owners",
        "Size annual hours saved, match the right Gemini Enterprise capability "
        "(Levels 1–6) and produce the Business Value Brief.",
        "Qualify a new use case",
        START_INTAKE,
    ),
    (
        "wm-tech",
        "**🏗️ Technical Architecture Review** — Phase 2, solution architects",
        "Pick a qualified opportunity waiting for review and generate the "
        "22-point Technical Architecture Dossier.",
        "Start a technical review",
        START_TECH_REVIEW,
    ),
    (
        "wm-portfolio",
        "**📊 CoE Portfolio Prioritization** — CoE leads",
        "Score every qualified opportunity on Business Value and Feasibility "
        "and segment them into Quick Wins, Strategic Bets, Departmental Niche "
        "and Deprioritized.",
        "Open portfolio review",
        OPEN_PORTFOLIO,
    ),
)


def build_welcome_menu(surface_id: str) -> list[dict[str, Any]]:
    """The A2UI messages for the inline three-workflow menu card."""
    children: list[str] = ["wm-title"]
    nodes: list[ui.Component] = [ui.text("wm-title", "What would you like to do?", "h3")]

    for i, (cid, title, description, label, event) in enumerate(_WORKFLOWS):
        children += [f"{cid}-rule", f"{cid}-title", f"{cid}-desc", f"{cid}-btn"]
        nodes += [
            ui.divider(f"{cid}-rule"),
            ui.text(f"{cid}-title", title, "body"),
            ui.text(f"{cid}-desc", description, "caption"),
            *ui.event_button(f"{cid}-btn", label, event, {}, primary=i == 0),
        ]

    children += ["wm-rule-end", "wm-hint"]
    nodes += [
        ui.divider("wm-rule-end"),
        ui.text(
            "wm-hint",
            "Or just describe your use case idea in chat to jump straight into the intake.",
            "caption",
        ),
    ]

    return ui.surface(surface_id, [ui.column(ROOT_ID, children), *nodes])
