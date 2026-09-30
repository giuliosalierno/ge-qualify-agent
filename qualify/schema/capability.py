"""GE App capability ladder.

Six levels, ordered by build complexity. This is the `🟩 GE App agentic
capabilities needed` inventory field and the primary output of docs/framework.md
Activity 2, which asks the agent to "map the requirement to the least complex
agentic capability".

Naming. docs/framework.md L61 lists six capabilities, but two of its labels no
longer match the Gemini Enterprise documentation:

    docs/framework.md label            | Actual GE App surface
    -------------------------------|----------------------------------------
    "Workflow Builder"             | Workflow Builder -> chat agent
    "Gemini Spark"                 | Workflow Builder -> workflow agent

Verified against the GE docs navigation on 2026-09-15: there is no "Agent
Designer" and no "Spark" anywhere in it. Workflow Builder is the no-code
builder, and it contains two distinct agent types. We use the documented
names and keep the docs/framework.md wording in `legacy_label` so the mapping
stays traceable.

Numbering. docs/framework.md L111 calls "Tiers 1-4" citizen-builder opportunities,
and levels 1-4 below are exactly the ones an end user can build unaided. That
boundary is independent corroboration that six is the intended length.
L156's reference to "Tier 5-7" does not reconcile with any enumerated list and
is treated as a documentation slip — flagged to the framework owner.
"""

from enum import IntEnum


class CapabilityLevel(IntEnum):
    """The least complex GE App capability that can deliver a use case."""

    DEFAULT_ASSISTANT = 1
    CUSTOM_SKILL = 2
    WORKFLOW_BUILDER_CHAT_AGENT = 3
    WORKFLOW_BUILDER_WORKFLOW_AGENT = 4
    WORKFLOW_AGENT_WITH_CUSTOM_MCP = 5
    HIGH_CODE_AGENT = 6

    @property
    def label(self) -> str:
        """Human-readable name, matching current GE App documentation."""
        return _LABELS[self]

    @property
    def legacy_label(self) -> str:
        """The wording used in docs/framework.md L61.

        Kept so a reader of the framework document can find the corresponding
        level without guessing.
        """
        return _LEGACY_LABELS[self]

    @property
    def is_citizen_builder(self) -> bool:
        """True when an end user can build this without a developer.

        The 1-4 / 5-6 split is docs/framework.md L111's "Tiers 1-4". Activity 2
        uses it to decide whether to hand the user step-by-step build
        instructions or route the case to the CoE backlog.
        """
        return self <= CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT

    @property
    def delivery_tier(self) -> "DeliveryTier":
        """Roll-up to the coarse three-tier ladder.

        design_plan.md 4.2 speaks in three tiers. That is too coarse to drive
        Activity 2 routing — it cannot tell a user to use a workflow agent
        rather than a chat agent — but it is the right granularity for an
        executive brief. So the agent picks a level and the tier is derived,
        never assigned.

        Levels 1-2 are No-Code: a custom skill is instructions and templates,
        no build. This must match the matrix in
        `skills/ge_capability_grounding/SKILL.md` (enforced by a test).
        """
        return _TIER_ROLLUP[self]


class DeliveryTier(IntEnum):
    """Coarse delivery tier for executive summaries. Always derived."""

    NO_CODE = 1
    LOW_CODE = 2
    PRO_CODE = 3

    @property
    def label(self) -> str:
        return _TIER_LABELS[self]


_LABELS = {
    CapabilityLevel.DEFAULT_ASSISTANT: "Default assistant",
    CapabilityLevel.CUSTOM_SKILL: "Assistant with custom skill",
    CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT: "Workflow Builder — chat agent",
    CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT: "Workflow Builder — workflow agent",
    CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP: "Workflow agent with custom MCP server",
    CapabilityLevel.HIGH_CODE_AGENT: "Custom high-code agent (ADK / A2A)",
}

_LEGACY_LABELS = {
    CapabilityLevel.DEFAULT_ASSISTANT: "default assistant",
    CapabilityLevel.CUSTOM_SKILL: "assistant with custom skills",
    CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT: "Workflow Builder",
    CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT: "Gemini Spark",
    CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP: "Workflow Builder with custom MCP",
    CapabilityLevel.HIGH_CODE_AGENT: "high-code agent",
}

_TIER_ROLLUP = {
    CapabilityLevel.DEFAULT_ASSISTANT: DeliveryTier.NO_CODE,
    CapabilityLevel.CUSTOM_SKILL: DeliveryTier.NO_CODE,
    CapabilityLevel.WORKFLOW_BUILDER_CHAT_AGENT: DeliveryTier.LOW_CODE,
    CapabilityLevel.WORKFLOW_BUILDER_WORKFLOW_AGENT: DeliveryTier.LOW_CODE,
    CapabilityLevel.WORKFLOW_AGENT_WITH_CUSTOM_MCP: DeliveryTier.PRO_CODE,
    CapabilityLevel.HIGH_CODE_AGENT: DeliveryTier.PRO_CODE,
}

_TIER_LABELS = {
    DeliveryTier.NO_CODE: "Tier 1: No-Code",
    DeliveryTier.LOW_CODE: "Tier 2: Low-Code",
    DeliveryTier.PRO_CODE: "Tier 3: Pro-Code",
}
