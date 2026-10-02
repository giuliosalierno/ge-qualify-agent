"""Server events raised by buttons in the interactive views.

Each maps onto a chat command the agent already understands, so a click and
a typed command take exactly the same path (see ``turn._try_view_event``).
"""

from __future__ import annotations

#: Opens the portfolio side panel. Same as typing ``portfolio review``.
OPEN_PORTFOLIO = "open_portfolio"

#: Starts Phase 2 for ``context.recordId``. Same as typing
#: ``technical review <recordId>``. Without a ``recordId`` it lists the
#: opportunities waiting for review, same as typing ``technical review``.
START_TECH_REVIEW = "start_tech_review"

#: Opens Stage 1 of the Business Value Intake. Raised by the welcome menu.
START_INTAKE = "start_intake"

#: Opens the brief side panel for ``context.recordId`` from the record store.
OPEN_BRIEF = "open_brief"

#: Opens this conversation's opportunity workspace. Same as typing
#: ``open workspace``.
OPEN_WORKSPACE = "open_workspace"

VIEW_EVENTS = frozenset(
    {OPEN_PORTFOLIO, START_TECH_REVIEW, START_INTAKE, OPEN_BRIEF, OPEN_WORKSPACE}
)
