"""A2A Agent Card definition for the GE Qualification Agent.

Declares A2UI v0.9 extension with the Gemini Enterprise composite catalog.
"""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    AuthorizationCodeOAuthFlow,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityScheme,
)
from a2ui.a2a.extension import get_a2ui_agent_extension

from qualify.a2ui.catalog import catalog_id

# The SDK constant is "0.9" while the wire version is "v0.9".
A2UI_SDK_VERSION = "0.9"
WIRE_VERSION = "v0.9"


def build_agent_card(base_url: str) -> AgentCard:
    """Builds the A2A agent card advertising A2UI v0.9 extension support and OAuth 2.0 user auth."""
    clean_base = base_url.rstrip("/")
    extension = get_a2ui_agent_extension(
        A2UI_SDK_VERSION,
        False,  # accepts_inline_catalogs - only use GE composite catalog by ID
        [catalog_id()],
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

    oauth_scheme = SecurityScheme(
        root=OAuth2SecurityScheme(
            description="Microsoft SharePoint Delegated User Login (On-Behalf-Of User)",
            flows=OAuthFlows(
                authorizationCode=AuthorizationCodeOAuthFlow(
                    authorizationUrl=f"{clean_base}/auth",
                    tokenUrl=f"{clean_base}/token",
                    scopes={
                        "https://graph.microsoft.com/Sites.ReadWrite.All": "Read and write SharePoint opportunities on behalf of the signed-in user",
                    },
                )
            ),
        )
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
        capabilities=AgentCapabilities(streaming=True, extensions=[extension]),
        skills=[skill],
        security_schemes={"microsoft_sharepoint_oauth": oauth_scheme},
        security=[{"microsoft_sharepoint_oauth": ["https://graph.microsoft.com/Sites.ReadWrite.All"]}],
    )
