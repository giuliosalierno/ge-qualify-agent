"""Activity 2: Capability tier classification & GCP-grounded solution routing.

Maps the collected business requirements and system footprint to the least
complex Gemini Enterprise agentic capability (Levels 1-6) per AGENT_PLAN.MD
Activity 2 and `skills/ge_capability_grounding/SKILL.md`.

Enforces the **Anti-Overcommitment & Pro-Code Delegation Rule**:
- Never promises No-Code (Levels 1-2) or Low-Code (Levels 3-4) when data sources
  are unknown (`"unknown"`), include unverified systems (`"other"`), require
  write/transactional mutations on read-only connectors, or involve complex
  multi-system orchestration.
- Whenever feasibility in No-Code/Low-Code is uncertain, delegates the target
  production build to **Pro-Code (`Level 5: Custom MCP` or `Level 6: High-Code ADK`)**
  and recommends a safe read-only Phase 0 citizen prototype alongside Gate 2
  Technical Architecture Review (`ge-review-tech`).
"""

from __future__ import annotations

from qualify.schema.capability import CapabilityLevel
from qualify.schema.use_case_record import UseCaseRecord

# Official Gemini Enterprise (Agentspace) Native Data Store Connectors (Read / RAG Grounding)
_NATIVE_READ_CONNECTORS = {
    "google drive",
    "drive",
    "gmail",
    "google docs",
    "google calendar",
    "bigquery",
    "cloud storage",
    "sharepoint",
    "onedrive",
    "jira",
    "confluence",
    "servicenow",
    "salesforce",
    "zendesk",
    "hubspot",
    "box",
    "slack",
    "github",
}

# Connectors with standard pre-built Connector Actions in Gemini Enterprise Workflow Builder
# (Standard create/update ticket, send message, or basic record action when admin-enabled)
_NATIVE_STANDARD_WRITE_CONNECTORS = {
    "gmail",
    "google calendar",
    "google docs",
    "jira",
    "servicenow",
    "salesforce",
    "zendesk",
    "slack",
}

# Systems that always require Pro-Code (Custom MCP Server on Cloud Run or ADK / A2A)
_HIGH_CODE_SYSTEMS = {
    "sap",
    "oracle",
    "workday",
    "mainframe",
    "snowflake",
    "databricks",
    "custom api",
    "on-prem",
    "sql",
    "erp",
    "postgres",
    "mysql",
    "mongodb",
    "graphql",
    "rest",
    "soap",
    "webhook",
}

# Verbs indicating linear workflow / action execution
_WORKFLOW_VERBS = (
    "automate",
    "trigger",
    "schedule",
    "step",
    "send email",
    "triage",
    "route",
    "notify",
    "assign",
)

# Verbs indicating external state mutation or transactional writes
_MUTATION_VERBS = (
    "write",
    "update record",
    "create ticket",
    "create record",
    "modify",
    "delete",
    "sync",
    "approve",
    "post to",
    "execute",
    "reconcile",
    "provision",
    "migrate",
    "push to",
    "commit",
    "mutate",
)

# Markers of non-linear orchestration, deterministic math/reconciliation, or strict SLAs
# that exceed Low-Code Workflow Builder boundaries per GCP reference documentation
_COMPLEX_ORCHESTRATION_MARKERS = (
    "multi-agent",
    "loop",
    "conditional branch",
    "rollback",
    "real-time transaction",
    "reconciliation",
    "orchestrate across",
    "two-way sync",
    "bidirectional",
    "custom schema",
)


def _assess_gcp_grounding_signals(record: UseCaseRecord) -> dict[str, object]:
    """Evaluates the record against GCP reference capability boundaries."""
    tech = record.technical
    raw_sources = [s.strip().lower() for s in tech.data_sources if s.strip()]
    has_unknown_source = "unknown" in raw_sources
    has_other_source = "other" in raw_sources or bool(
        tech.other_data_sources and tech.other_data_sources.strip()
    )

    named_sources = [s for s in raw_sources if s not in ("unknown", "other")]
    if tech.other_data_sources and tech.other_data_sources.strip():
        named_sources.append(tech.other_data_sources.strip().lower())

    classification = (tech.security.data_classification or "internal").lower()
    problem = (record.business.problem_description or "").lower()
    stories = (record.business.user_stories or "").lower()
    text_corpus = f"{problem} {stories}"

    has_high_code_sys = has_other_source or any(
        any(hc in src for hc in _HIGH_CODE_SYSTEMS) for src in named_sources
    )
    has_workflow_verbs = any(w in text_corpus for w in _WORKFLOW_VERBS)
    has_mutation_verbs = any(m in text_corpus for m in _MUTATION_VERBS)
    has_complex_orchestration = any(
        c in text_corpus for c in _COMPLEX_ORCHESTRATION_MARKERS
    )

    # Check if any named source lacks native write support when mutations are requested
    read_only_mutation_sources = [
        src
        for src in named_sources
        if has_mutation_verbs
        and not any(w in src for w in _NATIVE_STANDARD_WRITE_CONNECTORS)
    ]

    # Anti-overcommitment uncertainty reasons
    delegation_reasons: list[str] = []
    if has_unknown_source:
        delegation_reasons.append(
            "Target data sources are marked **Not sure yet (`unknown`)** — native Gemini Enterprise connector coverage cannot be assumed without verification."
        )
    if has_other_source:
        other_label = (tech.other_data_sources or "custom/unlisted system").strip()
        delegation_reasons.append(
            f"Involves unverified or custom system (**{other_label}**) outside the standard out-of-the-box Gemini Enterprise connector catalog."
        )
    if has_high_code_sys and not has_other_source:
        delegation_reasons.append(
            f"Touches enterprise backend(s) (**{', '.join(named_sources)}**) that require custom API/MCP tool adapters or VPC transit."
        )
    if read_only_mutation_sources:
        delegation_reasons.append(
            f"Workflow implies state mutations/writes across **{', '.join(read_only_mutation_sources)}**, where native Gemini Enterprise connectors are primarily read/retrieval-scoped."
        )
    if has_mutation_verbs and len(named_sources) >= 2:
        delegation_reasons.append(
            "Cross-system write/mutation across multiple repositories requires transactional error handling beyond Low-Code Workflow Builder."
        )
    if has_complex_orchestration:
        delegation_reasons.append(
            "Describes non-linear orchestration, reconciliation, or bidirectional sync that exceeds linear Low-Code Workflow Builder prompt chains."
        )
    if classification == "restricted":
        delegation_reasons.append(
            "Data classification is **Restricted**, requiring dedicated IAM/WIF, VPC Service Controls, and High-Code ADK guardrails."
        )

    return {
        "named_sources": named_sources,
        "classification": classification,
        "has_unknown_source": has_unknown_source,
        "has_other_source": has_other_source,
        "has_high_code_sys": has_high_code_sys,
        "has_workflow_verbs": has_workflow_verbs,
        "has_mutation_verbs": has_mutation_verbs,
        "has_complex_orchestration": has_complex_orchestration,
        "read_only_mutation_sources": read_only_mutation_sources,
        "delegation_reasons": delegation_reasons,
        "problem": problem,
    }


def classify_capability(record: UseCaseRecord, *, force_refresh: bool = False) -> None:
    """Assigns `capability_level` and GCP-grounded `capability_rationale` without overcommitting."""
    tech = record.technical
    is_legacy_rationale = bool(
        tech.capability_rationale
        and "GCP Reference" not in tech.capability_rationale
        and "Proposed Solution" not in tech.capability_rationale
    )
    if (
        not force_refresh
        and not is_legacy_rationale
        and tech.capability_level is not None
        and tech.capability_rationale
    ):
        return

    signals = _assess_gcp_grounding_signals(record)
    named_sources = list(signals["named_sources"])  # type: ignore[arg-type]
    classification = str(signals["classification"])
    has_unknown_source = bool(signals["has_unknown_source"])
    has_high_code_sys = bool(signals["has_high_code_sys"])
    has_workflow_verbs = bool(signals["has_workflow_verbs"])
    has_mutation_verbs = bool(signals["has_mutation_verbs"])
    has_complex_orchestration = bool(signals["has_complex_orchestration"])
    read_only_mutation_sources = list(signals["read_only_mutation_sources"])  # type: ignore[arg-type]
    delegation_reasons = list(signals["delegation_reasons"])  # type: ignore[arg-type]
    problem = str(signals["problem"])

    if tech.capability_level is None or force_refresh or is_legacy_rationale:
        # 1. Hard Pro-Code ADK (Level 6) triggers
        if (
            classification == "restricted"
            or has_complex_orchestration
            or (has_high_code_sys and len(named_sources) >= 2)
            or (has_mutation_verbs and len(named_sources) >= 2)
        ):
            tech.capability_level = CapabilityLevel.HIGH_CODE_AGENT
        # 2. Pro-Code Custom MCP (Level 5) triggers — including Anti-Overcommitment Delegation!
        elif (
            has_high_code_sys
            or has_unknown_source
            or bool(read_only_mutation_sources)
            or len(named_sources) >= 3
        ):
            tech.capability_level = CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP
        # 3. Low-Code Workflow Builder — Workflow Agent (Level 4)
        elif len(named_sources) >= 1 and (has_workflow_verbs or has_mutation_verbs):
            tech.capability_level = CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT
        # 4. Low-Code Workflow Builder — Chat Agent (Level 3)
        elif len(named_sources) >= 1:
            tech.capability_level = CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT
        # 5. No-Code Assistant with Custom Skill (Level 2)
        elif len(problem) > 80:
            tech.capability_level = CapabilityLevel.CUSTOM_SKILL
        # 6. No-Code Default Assistant (Level 1)
        else:
            tech.capability_level = CapabilityLevel.DEFAULT_ASSISTANT

    level = tech.capability_level
    if not tech.capability_rationale or force_refresh or is_legacy_rationale:
        tech.capability_rationale = _build_guidance(
            level,
            named_sources,
            classification,
            delegation_reasons=delegation_reasons,
            record_id=record.meta.record_id,
        )


_PRETTY_SOURCE_MAP: dict[str, str] = {
    "google_drive": "Google Drive / Docs",
    "gmail_calendar": "Gmail & Google Calendar",
    "sharepoint": "Microsoft SharePoint",
    "sharepoint_onedrive": "Microsoft SharePoint / OneDrive",
    "confluence": "Atlassian Confluence",
    "jira": "Atlassian Jira",
    "salesforce": "Salesforce CRM",
    "servicenow": "ServiceNow",
    "bigquery": "Google BigQuery",
    "cloud_sql": "Google Cloud SQL / AlloyDB",
    "sap_erp": "SAP ERP",
    "workday": "Workday",
    "zendesk": "Zendesk",
    "slack_teams": "Slack / Microsoft Teams",
    "public_web": "Public Web Grounding",
}


def _pretty_sources(sources: list[str]) -> str:
    if not sources:
        return "user-provided documents / general knowledge"
    return ", ".join(
        _PRETTY_SOURCE_MAP.get(s, s.replace("_", " ").title()) for s in sources
    )


def _build_guidance(
    level: CapabilityLevel,
    sources: list[str],
    classification: str,
    *,
    delegation_reasons: list[str] | None = None,
    record_id: str = "UC-XXXX",
) -> str:
    src_display = _pretty_sources(sources)
    reasons = delegation_reasons or []

    if level == CapabilityLevel.DEFAULT_ASSISTANT:
        return (
            "**Proposed Solution — Level 1: Default Assistant (`Tier 1: No-Code / Out-of-the-Box`)**\n"
            "- **GCP Reference Scope:** Supported immediately via Gemini Enterprise standard chat over user-uploaded files (<=50 MB) or default Google Workspace search (`Gmail`, `Drive`, `Docs`).\n"
            "- **What Is Covered vs. Bounded:** Covers interactive Q&A, summarization, and drafting. Does **not** perform automated background triggers or external system writes.\n"
            "- **Actionable Setup:** Users can start immediately in Gemini Enterprise chat by attaching relevant files. If dedicated third-party connectors or write actions are later required, run `technical review "
            f"{record_id}` to re-scope."
        )

    if level == CapabilityLevel.CUSTOM_SKILL:
        return (
            "**Proposed Solution — Level 2: Assistant with Custom Skill (`Tier 1: No-Code / Citizen Builder`)**\n"
            "- **GCP Reference Scope:** Uses a shared Gemini Enterprise **Custom Skill / Gem** with standardized system instructions, domain rubric, and output templates.\n"
            "- **What Is Covered vs. Bounded:** Standardizes repeatable reasoning and formatting across the team without backend code. Does **not** bind dedicated third-party data store indexes or execute API writes.\n"
            "- **Actionable Setup:**\n"
            "  1. Open Gemini Enterprise -> **Custom Skills / Gems** and paste your team's operating rules and output template.\n"
            "  2. Share the skill with the target user group. *(Guardrail: If live third-party system retrieval is needed later, escalate to Level 3 Low-Code or Pro-Code)*."
        )

    if level == CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT:
        return (
            f"**Proposed Solution — Level 3: Workflow Builder Chat Agent (`Tier 2: Low-Code`)**\n"
            f"- **GCP Reference Scope:** Grounded conversational RAG agent in **Gemini Enterprise Workflow Builder** bound to native Data Store Connector(s) for **{src_display}**.\n"
            f"- **What Is Covered vs. Bounded (No Overcommitment):**\n"
            f"  - ✅ **Natively Supported:** Read-only indexed/federated retrieval and grounded Q&A over **{src_display}** with source citations.\n"
            f"  - ⚠️ **Guardrail Boundary:** Native data store connectors are subject to indexing sync intervals and are **read-only** unless paired with an admin-enabled Connector Action. If this use case requires custom field mutations or sub-minute real-time database queries, **delegate to Pro-Code (`Level 5/6`)**.\n"
            f"- **Actionable Setup:**\n"
            f"  1. In Gemini Enterprise **Workflow Builder**, create a **Chat Agent** and attach the verified **{src_display}** data store(s).\n"
            f"  2. Verify document-level ACL preservation with your workspace admin before publishing."
        )

    if level == CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT:
        return (
            f"**Proposed Solution — Level 4: Workflow Builder Workflow Agent (`Tier 2: Low-Code`)**\n"
            f"- **GCP Reference Scope:** Linear, sequential multi-step flow in **Gemini Enterprise Workflow Builder** connecting **{src_display}** with structured prompt steps and pre-built native **Connector Actions**.\n"
            f"- **What Is Covered vs. Bounded (No Overcommitment):**\n"
            f"  - ✅ **Natively Supported:** Deterministic step-by-step prompt chaining, retrieval from **{src_display}**, and standard connector actions (e.g., drafting/sending notifications or creating standard tickets if enabled by IT).\n"
            f"  - ⚠️ **Guardrail Boundary:** Workflow Builder does **not** support cyclic multi-agent loops, transactional rollback across systems, or custom REST payload schemas. If the required action on **{src_display}** involves custom fields or non-standard writes, **do not force Low-Code — delegate to Pro-Code (`Level 5 Custom MCP`)**.\n"
            f"- **Actionable Setup:**\n"
            f"  1. Build a linear prototype flow in **Workflow Builder** and verify whether the target Connector Action is enabled in your tenant.\n"
            f"  2. If any connector action is missing or read-only, run `technical review {record_id}` to scope a custom MCP tool server."
        )

    reason_bullets = (
        "\n".join(f"  - {r}" for r in reasons)
        if reasons
        else f"  - Integration scope across **{src_display}** (`{classification}`) requires custom tool schemas or unverified API actions that cannot be safely guaranteed in No-Code/Low-Code."
    )

    if level == CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP:
        return (
            f"**Proposed Solution — Level 5: Workflow Agent with Custom MCP Server (`Tier 3: Pro-Code — Delegated`)**\n"
            f"- **Proposed Architecture (GCP Reference):** Gemini Enterprise agent connected to a custom **Model Context Protocol (MCP)** tool server deployed on **Cloud Run** (using Service Account IAM / Workload Identity Federation) to interface with **{src_display}**.\n"
            f"- **Why Delegated to Pro-Code (Anti-Overcommitment Assessment):**\n"
            f"{reason_bullets}\n"
            f"- **CoE Implementation & Registration Blueprint (`google-agents-cli`):**\n"
            f"  - Scaffold prototype: `agents-cli scaffold create <project-name> --agent adk --deployment-target cloud_run --prototype`\n"
            f"  - Publish to Gemini Enterprise: `agents-cli publish gemini-enterprise --registration-type a2a --agent-card-url <service-url>/.well-known/agent-card.json`\n"
            f"- **Next Steps & Safe Prototype Path:**\n"
            f"  1. **Mandatory Gate 2 Review:** Run **`technical review {record_id}`** (`ge-review-tech`) with a Lead Solution Architect to verify API schemas, authentication, and MCP hosting.\n"
            f"  2. **Safe Phase 0 No-Code Prototype:** While the CoE scopes the MCP server, business users can validate prompt instructions using a **Level 2 Custom Skill** with manually uploaded sample exports (read-only)."
        )

    return (
        f"**Proposed Solution — Level 6: Custom High-Code Agent on ADK (`Tier 3: Pro-Code — Delegated`)**\n"
        f"- **Proposed Architecture (GCP Reference):** Custom **Vertex AI Agent Development Kit (ADK)** Python multi-agent system (`LlmAgent` / `SequentialAgent`) deployed on **Vertex AI Agent Runtime (`--deployment-target agent_runtime`)** or **Cloud Run (`--deployment-target cloud_run`)** for **{src_display}** (`{classification}`).\n"
        f"  - *Choose **Agent Runtime** (`--registration-type adk`, native `:streamQuery`)* if the agent requires managed user-scoped OAuth 2.0 consent (`--authorization-id`), managed `VertexAiSessionService`, or Private Service Connect Interface (`--network-attachment`).\n"
        f"  - *Choose **Cloud Run** (`--registration-type a2a`, `--agent-card-url`)* if the agent requires custom container dependencies, Direct VPC Egress, or Eventarc/PubSub push triggers (`/apps/{{app}}/trigger/*`).\n"
        f"- **Why Delegated to Pro-Code (Anti-Overcommitment Assessment):**\n"
        f"{reason_bullets}\n"
        f"- **CoE Implementation & Registration Blueprint (`google-agents-cli`):**\n"
        f"  - Scaffold prototype first: `agents-cli scaffold create <project-name> --agent adk --deployment-target agent_runtime --prototype`\n"
        f"  - Deploy & publish: `agents-cli deploy` → `agents-cli publish gemini-enterprise --registration-type adk`\n"
        f"- **Next Steps & Safe Prototype Path:**\n"
        f"  1. **Mandatory Gate 2 Review:** Initiate **`technical review {record_id}`** (`ge-review-tech`) to evaluate the 22 technical subcriteria (network transit, WIF/IAM, data residency, and latency SLAs).\n"
        f"  2. **Safe Phase 0 No-Code Prototype:** Build a read-only **Level 2/3 Gemini Enterprise prototype** over sanitized sample documents to validate reasoning quality before committing engineering sprint capacity."
    )
