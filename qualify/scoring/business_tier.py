"""Activity 2: Capability tier classification for Business Intake.

Maps the collected business requirements and system footprint to the least
complex Gemini Enterprise agentic capability (Levels 1-6) per AGENT_PLAN.MD
Activity 2, and generates actionable citizen-builder or CoE handover guidance.
"""

from __future__ import annotations

from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import UseCaseRecord

# Systems typically served by out-of-the-box or native GE connectors
_NATIVE_CONNECTORS = {
    "google drive",
    "drive",
    "gmail",
    "google docs",
    "sharepoint",
    "jira",
    "confluence",
    "servicenow",
    "salesforce",
}

# Systems that typically require custom MCP servers or high-code A2A integration
_HIGH_CODE_SYSTEMS = {
    "sap",
    "oracle",
    "mainframe",
    "snowflake",
    "databricks",
    "custom api",
    "on-prem",
    "sql",
    "erp",
}


def classify_capability(record: UseCaseRecord) -> None:
    """Assigns `capability_level` and `capability_rationale` if not already set."""
    tech = record.technical
    if tech.capability_level is not None and tech.capability_rationale:
        return

    sources = [s.strip().lower() for s in tech.data_sources if s.strip() and s != "other"]
    if tech.other_data_sources and tech.other_data_sources.strip():
        sources.append(tech.other_data_sources.strip().lower())
    classification = (tech.security.data_classification or "internal").lower()
    problem = (record.business.problem_description or "").lower()
    stories = (record.business.user_stories or "").lower()
    text_corpus = f"{problem} {stories}"

    has_high_code_sys = (
        bool(tech.other_data_sources)
        or "other" in tech.data_sources
        or any(any(hc in src for hc in _HIGH_CODE_SYSTEMS) for src in sources)
    )
    has_workflow_verbs = any(
        w in text_corpus
        for w in ("automate", "trigger", "schedule", "step", "update", "send email", "triage", "route")
    )

    if tech.capability_level is None:
        if classification == "restricted" or (has_high_code_sys and len(sources) >= 2):
            tech.capability_level = CapabilityLevel.HIGH_CODE_AGENT
        elif has_high_code_sys or len(sources) >= 3:
            tech.capability_level = CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP
        elif len(sources) >= 1 and has_workflow_verbs:
            tech.capability_level = CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT
        elif len(sources) >= 1:
            tech.capability_level = CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT
        elif len(problem) > 80:
            tech.capability_level = CapabilityLevel.CUSTOM_SKILL
        else:
            tech.capability_level = CapabilityLevel.DEFAULT_ASSISTANT

    level = tech.capability_level
    if not tech.capability_rationale:
        tech.capability_rationale = _build_guidance(level, sources, classification)


def _build_guidance(
    level: CapabilityLevel, sources: list[str], classification: str
) -> str:
    src_display = ", ".join(sources) if sources else "general enterprise knowledge"
    if level == CapabilityLevel.DEFAULT_ASSISTANT:
        return (
            "**Level 1 — Default Assistant (Citizen Builder / No-Code):**\n"
            "This use case can be delivered immediately using Gemini Enterprise out-of-the-box chat. "
            "Users can upload or reference relevant documents directly in their session without custom configuration."
        )
    if level == CapabilityLevel.CUSTOM_SKILL:
        return (
            "**Level 2 — Assistant with Custom Skill (Citizen Builder / No-Code):**\n"
            "1. Open Gemini Enterprise and create a new **Custom Skill / Gem**.\n"
            "2. Paste your team's standard operating instructions, tone guidelines, and output templates into the system instructions.\n"
            "3. Share the skill link with your team for immediate productivity gains."
        )
    if level == CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT:
        return (
            f"**Level 3 — Workflow Builder Chat Agent (Citizen Builder / Low-Code):**\n"
            f"1. Open **Workflow Builder** in Gemini Enterprise and select **Chat Agent**.\n"
            f"2. Enable the native data connector(s) for **{src_display}**.\n"
            f"3. Configure grounded retrieval instructions so the agent answers user questions strictly from those repositories."
        )
    if level == CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT:
        return (
            f"**Level 4 — Workflow Builder Workflow Agent (Citizen Builder / Low-Code):**\n"
            f"1. Open **Workflow Builder** in Gemini Enterprise and select **Workflow Agent**.\n"
            f"2. Wire a multi-step flow connecting **{src_display}** with structured prompt steps.\n"
            f"3. Test with 2–3 sample tasks before publishing to the department."
        )
    if level == CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP:
        return (
            f"**Level 5 — Workflow Agent with Custom MCP Server (CoE / Pro-Code):**\n"
            f"Because this workflow spans **{src_display}** (Classification: `{classification}`), it requires a custom MCP tool server.\n"
            f"- **Next Step:** Hand over this Record ID to the **Technical Architecture Review Agent (`ge-review-tech`)** to scope API schemas, IAM service accounts, and MCP hosting."
        )
    return (
        f"**Level 6 — Custom High-Code Agent on ADK / Cloud Run (CoE / Pro-Code):**\n"
        f"This use case involves complex multi-system orchestration across **{src_display}** and `{classification}` data governance.\n"
        f"- **Next Step:** Initiate **Activity 4: High-Code Technical Architecture Review (`ge-review-tech`)** with a Lead Solution Architect to evaluate the 22 technical subcriteria (network transit, WIF/IAM, and data residency)."
    )
