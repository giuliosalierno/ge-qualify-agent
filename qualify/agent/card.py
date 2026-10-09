"""A2A Agent Card definition for the GE Qualification Agent.

Declares A2UI v0.9 extension with the Gemini Enterprise composite catalog.
"""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentSkill,
)
from a2ui.a2a.extension import get_a2ui_agent_extension

from qualify.a2ui.catalog import catalog_id

# The SDK constant is "0.9" while the wire version is "v0.9".
A2UI_SDK_VERSION = "0.9"
WIRE_VERSION = "v0.9"

STARTER_PROMPTS_EXTENSION_URI = (
    "https://www.googleapis.com/gemini-enterprise/a2a/extensions/starter_prompts/v1"
)
STARTER_PROMPTS = [
    "Start a Business Value Intake (Phase 1)",
    "Run a Technical Architecture Review (Phase 2)",
    "Run a Portfolio Prioritization (Phase 3)",
]


def build_agent_card(base_url: str) -> AgentCard:
    """Builds the A2A agent card advertising A2UI v0.9 extension support.

    No OAuth security scheme is declared. Gemini Enterprise does not forward
    a delegated Microsoft token over A2A, so the scheme (and its `/token`
    endpoint) never carried a user identity. SharePoint sign-in happens through
    the per-conversation signed link instead (see `qualify/connectors/oauth_state.py`).
    """
    extension = get_a2ui_agent_extension(
        A2UI_SDK_VERSION,
        False,  # accepts_inline_catalogs - only use GE composite catalog by ID
        [catalog_id()],
    )
    starter_prompts_ext = AgentExtension(
        uri=STARTER_PROMPTS_EXTENSION_URI,
        description="Google Gemini Enterprise starter prompts extension to show contextually aware prompts on chat start.",
        params={"prompts": STARTER_PROMPTS},
    )

    skill = AgentSkill(
        id="ge_qualify",
        name="Gemini Enterprise Use Case Qualification",
        description=(
            "Guides stakeholders through a structured 4-stage business intake "
            "qualification interview with a dynamic A2UI living form. Sizes "
            "annualized team hours saved, classifies GE app agentic capabilities, "
            "and generates a qualified business brief for Centre of Excellence review."
        ),
        tags=["a2ui", "qualification", "intake", "gemini-enterprise"],
        examples=[
            "Qualify my use case",
            "Start business intake interview",
            "I want to qualify a claims triage agent",
        ],
    )

    return AgentCard(
        name="GE Use Case Qualification Agent",
        description=(
            "Gemini Enterprise qualification agent. Conducts business intake and "
            "sizing interviews with an interactive A2UI living form."
        ),
        url=base_url,
        version="0.1.0",
        default_input_modes=["text", "text/plain"],
        default_output_modes=["text", "text/plain"],
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[extension, starter_prompts_ext],
        ),
        skills=[skill],
    )
