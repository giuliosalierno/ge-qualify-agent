"""Activity 2: Capability tier classification & GCP-grounded solution routing.

Maps the collected business requirements and system footprint to the least
complex Gemini Enterprise agentic capability (Levels 1-6) per docs/framework.md
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

import re

from qualify.schema.capability import CapabilityLevel
from qualify.schema.classification import REGULATED, normalise_classification
from qualify.schema.use_case_record import UseCaseRecord
from qualify.scoring.connectors import (
    ConnectorMatch,
    catalog_marker,
    load_catalog,
    resolve,
)

# Which sources are native, which have documented actions, and which need a
# custom MCP server is NOT decided here: it comes from the cited connector
# catalog, qualify/scoring/connectors.yaml (see qualify/scoring/connectors.py).
# The hard-coded lists that used to live here assumed any source not on a
# deny list was native, which classified "Internal REST API" as a native
# Gemini Enterprise connector.

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


def _source_matches(record: UseCaseRecord) -> list[ConnectorMatch]:
    """Every named data source on the record, placed in the connector catalog.

    "Other" ticked without a name is kept as an unverified placeholder, so it
    still blocks a native-connector promise.
    """
    tech = record.technical
    raw = [s.strip() for s in tech.data_sources if s.strip()]
    names = [s for s in raw if s.lower() not in ("unknown", "other")]
    other = (tech.other_data_sources or "").strip()
    if other:
        names += [p.strip() for p in re.split(r"[,;\n]| and ", other) if p.strip()]
    elif any(s.lower() == "other" for s in raw):
        names.append("custom/unlisted system")
    return [resolve(n) for n in names]


def _cite(m: ConnectorMatch) -> str:
    return f"[{m.label}]({m.doc})" if m.doc else f"**{m.label}**"


def _assess_gcp_grounding_signals(record: UseCaseRecord) -> dict[str, object]:
    """Evaluates the record against GCP reference capability boundaries."""
    tech = record.technical
    raw_sources = [s.strip().lower() for s in tech.data_sources if s.strip()]
    has_unknown_source = "unknown" in raw_sources

    matches = _source_matches(record)
    named_sources = [m.id or m.source for m in matches]
    custom = [m for m in matches if m.status == "custom"]
    unverified = [m for m in matches if m.status == "unverified"]
    has_other_source = bool(unverified)

    classification = (
        normalise_classification(tech.security.data_classification) or "internal"
    )
    problem = (record.business.problem_description or "").lower()
    stories = (record.business.user_stories or "").lower()
    text_corpus = f"{problem} {stories}"

    # Custom and unverified sources both rule out a native-connector build.
    has_high_code_sys = bool(custom) or bool(unverified)
    has_workflow_verbs = any(w in text_corpus for w in _WORKFLOW_VERBS)
    has_mutation_verbs = any(m in text_corpus for m in _MUTATION_VERBS)
    has_complex_orchestration = any(
        c in text_corpus for c in _COMPLEX_ORCHESTRATION_MARKERS
    )

    # Writes are only native where the connector page documents actions.
    read_only_mutation_sources = [
        m.label for m in matches if has_mutation_verbs and m.native and not m.actions
    ]

    # Anti-overcommitment uncertainty reasons, each traceable to a source.
    delegation_reasons: list[str] = []
    if has_unknown_source:
        delegation_reasons.append(
            "Target data sources are marked **Not sure yet (`unknown`)** — native Gemini Enterprise connector coverage cannot be assumed without verification."
        )
    for m in unverified:
        delegation_reasons.append(
            f"Involves unverified or custom system (**{m.source}**) outside the standard out-of-the-box Gemini Enterprise connector catalog"
            + (f" — {m.note}" if m.note and m.id else "")
            + "."
        )
    if custom:
        catalog = load_catalog()
        delegation_reasons.append(
            f"Touches enterprise backend(s) (**{', '.join(m.label for m in custom)}**) with no native Gemini Enterprise connector "
            f"([connector catalog]({catalog.source_index}), reviewed {catalog.reviewed}); they require a "
            f"[custom MCP server]({catalog.custom_mcp_doc}) or API adapter."
        )
    if read_only_mutation_sources:
        delegation_reasons.append(
            f"Workflow implies state mutations/writes across **{', '.join(read_only_mutation_sources)}**, whose native Gemini Enterprise connectors document no end-user actions (read/retrieval-scoped)."
        )
    if has_mutation_verbs and len(named_sources) >= 2:
        delegation_reasons.append(
            "Cross-system write/mutation across multiple repositories requires transactional error handling beyond Low-Code Workflow Builder."
        )
    if has_complex_orchestration:
        delegation_reasons.append(
            "Describes non-linear orchestration, reconciliation, or bidirectional sync that exceeds linear Low-Code Workflow Builder prompt chains."
        )
    if classification == REGULATED:
        delegation_reasons.append(
            "Data classification is **Restricted / regulated**, requiring dedicated IAM/WIF, VPC Service Controls, and High-Code ADK guardrails."
        )

    return {
        "named_sources": named_sources,
        "matches": matches,
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


def _evidence(matches: list[ConnectorMatch]) -> str:
    """The "where does this come from" block appended to every rationale."""
    lines = [f"- **Grounding ({catalog_marker()}):**"]
    if not matches:
        lines.append("  - No external data sources named.")
    for m in matches:
        if m.status == "native":
            what = "native connector" + (
                ", end-user actions documented" if m.actions else ", read / ingest only"
            )
        elif m.status == "custom":
            what = "not in the GE connector catalog — custom MCP server needed"
        else:
            what = "not confirmed in the GE connector catalog — treated as custom"
        note = f" ({m.note})" if m.note and m.id else ""
        lines.append(f"  - {_cite(m)}: {what}{note}.")
    return "\n".join(lines)


def classify_capability(record: UseCaseRecord, *, force_refresh: bool = False) -> None:
    """Assigns `capability_level` and GCP-grounded `capability_rationale` without overcommitting.

    A stored classification is reused only if it was made against the current
    connector catalog. Bumping ``reviewed`` in connectors.yaml therefore
    re-classifies every record the next time it is scored — which is how a
    catalog correction reaches records saved before it.
    """
    tech = record.technical
    is_legacy_rationale = bool(
        tech.capability_rationale
        and catalog_marker() not in tech.capability_rationale
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
    matches: list[ConnectorMatch] = list(signals["matches"])  # type: ignore[arg-type]
    classification = str(signals["classification"])
    has_unknown_source = bool(signals["has_unknown_source"])
    has_high_code_sys = bool(signals["has_high_code_sys"])
    has_workflow_verbs = bool(signals["has_workflow_verbs"])
    has_mutation_verbs = bool(signals["has_mutation_verbs"])
    has_complex_orchestration = bool(signals["has_complex_orchestration"])
    read_only_mutation_sources = list(signals["read_only_mutation_sources"])  # type: ignore[arg-type]
    delegation_reasons = list(signals["delegation_reasons"])  # type: ignore[arg-type]
    problem = str(signals["problem"])

    # SKILL.md: Level 5 is one orchestrator calling a custom MCP server; Level 6
    # is for multi-system orchestration. One custom system beside native
    # connectors is therefore L5; two or more systems without a native
    # connector (each its own adapter) is L6.
    non_native_count = sum(1 for m in matches if not m.native)

    if tech.capability_level is None or force_refresh or is_legacy_rationale:
        # 1. Hard Pro-Code ADK (Level 6) triggers
        if (
            classification == REGULATED
            or has_complex_orchestration
            or non_native_count >= 2
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
        tech.capability_rationale = (
            _build_guidance(
                level,
                [m.label for m in matches],
                classification,
                delegation_reasons=delegation_reasons,
                record_id=record.meta.record_id,
            )
            + "\n"
            + _evidence(matches)
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
    def _pretty(s: str) -> str:
        if s in _PRETTY_SOURCE_MAP:
            return _PRETTY_SOURCE_MAP[s]
        # Catalog labels and user-typed names already carry their own casing.
        if any(c.isupper() for c in s):
            return s
        return s.replace("_", " ").title()

    return ", ".join(_pretty(s) for s in sources)


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


# ---------------------------------------------------------------------------
# Business-audience summary
# ---------------------------------------------------------------------------
# The rationale above is written for Solution Architects (MCP, Cloud Run, IAM,
# CLI commands) and belongs in the Technical Architecture Dossier. The Business
# Value Brief gets this plain-language version instead, built from the same
# connector check so the two never disagree.

#: level -> (what we'd build, who builds it)
_BUSINESS_APPROACH: dict[CapabilityLevel, tuple[str, str]] = {
    CapabilityLevel.DEFAULT_ASSISTANT: (
        "Use the standard Gemini Enterprise assistant — nothing to build.",
        "Your team, starting today.",
    ),
    CapabilityLevel.CUSTOM_SKILL: (
        "A shared Gemini Enterprise assistant set up with your team's "
        "instructions and templates.",
        "Your team, no coding needed.",
    ),
    CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT: (
        "A chat agent that answers from your connected systems, built in "
        "Gemini Enterprise's low-code Workflow Builder.",
        "Your team, with light support from the AI CoE.",
    ),
    CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT: (
        "An agent that runs a step-by-step workflow across your connected "
        "systems, built in the low-code Workflow Builder.",
        "Your team, with support from the AI CoE.",
    ),
    CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP: (
        "A Gemini Enterprise agent plus a custom connector for the systems "
        "that don't have a built-in one.",
        "AI CoE developers, after a technical review.",
    ),
    CapabilityLevel.HIGH_CODE_AGENT: (
        "A custom-built agent that coordinates several systems.",
        "AI CoE developers, after a technical review.",
    ),
}


#: Short form for summary lines.
_SHORT_APPROACH: dict[CapabilityLevel, str] = {
    CapabilityLevel.DEFAULT_ASSISTANT: "Standard Gemini Enterprise assistant",
    CapabilityLevel.CUSTOM_SKILL: "Shared assistant with team instructions",
    CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT: "Low-code chat agent",
    CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT: "Low-code workflow agent",
    CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP: "Agent plus a custom connector",
    CapabilityLevel.HIGH_CODE_AGENT: "Custom-built multi-system agent",
}


def approach_label(level: CapabilityLevel | None) -> str:
    """One-line approach for summaries, e.g. "Low-code chat agent (Level 3 of 6)"."""
    if level is None:
        return "To be assessed by the AI CoE"
    return f"{_SHORT_APPROACH[level]} (Level {level.value} of 6)"


def _names(matches: list[ConnectorMatch]) -> str:
    labels = list(dict.fromkeys(m.label for m in matches))
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def business_summary(record: UseCaseRecord) -> str:
    """Plain-language "what we'd build and why" for the Business Value Brief."""
    level = record.technical.capability_level
    if level is None:
        return (
            "- **Recommended approach:** to be assessed by the AI CoE once the "
            "systems involved are known."
        )

    signals = _assess_gcp_grounding_signals(record)
    matches: list[ConnectorMatch] = list(signals["matches"])  # type: ignore[arg-type]
    native = [m for m in matches if m.native]
    not_native = [m for m in matches if not m.native]
    read_only = list(signals["read_only_mutation_sources"])  # type: ignore[arg-type]

    why: list[str] = []
    if native:
        verb = "has" if len({m.label for m in native}) == 1 else "have"
        why.append(f"{_names(native)} {verb} a built-in Gemini Enterprise connector.")
    if not_native:
        verb = "doesn't" if len({m.label for m in not_native}) == 1 else "don't"
        why.append(
            f"{_names(not_native)} {verb}, so a developer needs to build a "
            "connector for it."
        )
    if signals["has_unknown_source"]:
        why.append(
            "Some systems aren't confirmed yet, so we plan for a custom "
            "connector until the technical review confirms them."
        )
    if read_only:
        why.append(
            f"The work needs to update {', '.join(read_only)}, which the "
            "built-in connector can only read."
        )
    if signals["has_complex_orchestration"]:
        why.append("The process has branching or two-way syncing steps.")
    if signals["classification"] == REGULATED:
        why.append(
            "The data is classified restricted or regulated, which needs extra "
            "security controls."
        )
    if not why:
        why.append("No external systems are needed beyond your documents and Google Workspace.")

    what, who = _BUSINESS_APPROACH[level]
    pro_code = level in (
        CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP,
        CapabilityLevel.HIGH_CODE_AGENT,
    )
    lines = [
        f"- **What we'd build:** {what} *(Level {level.value} of 6)*",
        f"- **Who builds it:** {who}",
        f"- **Why:** {' '.join(why)}",
    ]
    if pro_code:
        lines.append(
            "- **Meanwhile:** the team can try the core idea in the standard "
            "Gemini Enterprise assistant with sample documents, read-only."
        )
        lines.append(
            "- **Next step:** a technical review with a Solution Architect to "
            "confirm how each system is reached."
        )
    elif level in (
        CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT,
        CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT,
    ):
        lines.append(
            "- **Next step:** ask your Gemini Enterprise admin to connect the "
            "systems above, then build and pilot the agent with a small group."
        )
    else:
        lines.append(
            "- **Next step:** try it with a few colleagues and share what works."
        )
    return "\n".join(lines)
