"""Deployment-level settings shared across the agent.

Kept free of project-specific defaults on purpose: the same image runs in the
maintainer's project and in every go/demos Click-to-Deploy sandbox, so a
hardcoded URL here would silently point one deployment at another.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_warned_missing_agent_url = False


def agent_base_url() -> str:
    """Public base URL of this service, without a trailing slash.

    Used for the agent card and for every link the agent puts in chat (sign-in
    pages, callbacks). Resolution order:

    1. ``AGENT_URL`` (set by the deploy script / Terraform).
    2. ``http://localhost:$PORT`` for local runs.

    On Cloud Run (``K_SERVICE`` set) a missing ``AGENT_URL`` is a deployment
    bug: links would point at localhost. It is logged once, loudly, rather than
    papered over with another deployment's URL.
    """
    global _warned_missing_agent_url
    value = os.environ.get("AGENT_URL", "").strip()
    if value:
        return value.rstrip("/")
    if os.environ.get("K_SERVICE") and not _warned_missing_agent_url:
        _warned_missing_agent_url = True
        log.error(
            "AGENT_URL is not set on Cloud Run service %s; in-chat links will "
            "point at localhost. Set AGENT_URL to the service's public URL.",
            os.environ.get("K_SERVICE"),
        )
    return f"http://localhost:{os.environ.get('PORT', '8080')}"


def interactive_views_enabled() -> bool:
    """Whether to send side-panel views instead of long markdown replies.

    On unless ``INTERACTIVE_VIEWS`` is ``0``/``false``/``off``. The switch
    exists because the views depend on GE rendering components (``Canvas``,
    ``VegaChart``, ...) that are not part of the A2UI base catalog; if a GE
    release stops drawing them, this restores the markdown replies without a
    code change.
    """
    return os.environ.get("INTERACTIVE_VIEWS", "1").strip().lower() not in ("0", "false", "off")


def welcome_menu_enabled() -> bool:
    """Whether a greeting opens the three-workflow menu card.

    On unless ``WELCOME_MENU`` is ``0``/``false``/``off``. When off, a greeting
    goes straight to the Business Value Intake Stage 1 card, as it used to.
    """
    return os.environ.get("WELCOME_MENU", "1").strip().lower() not in ("0", "false", "off")
