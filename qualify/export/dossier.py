"""Markdown Technical Architecture Dossier & Access Checklist.

The Phase 2 counterpart to `brief.py`, implementing the Turn 6 template in
`skills/ge_tech_review/SKILL.md` and design plan §8.2.

Two rules carried over from the SKILL:

**Zero extrapolation.** Anything the review did not establish renders as a
literal `⚠️ [Pending Stage N Discovery]` marker, never as a plausible guess. A
dossier that quietly invents a transit path is worse than one with visible
holes, because the holes are what the access checklist is for.

**Blockers lead.** If the review found an airgap or a cloud ban, that appears
above the score, not buried in a table. A reader who stops after the header
should still learn the one thing that decides the project.
"""

from __future__ import annotations

from datetime import date

from qualify.schema.use_case_record import UseCaseRecord
from qualify.scoring.technical import (
    FAIL,
    KEY2_THRESHOLD,
    PASS,
    TechnicalScore,
    score_technical,
)

#: What an unestablished field renders as. Matches the SKILL exactly.
PENDING = "⚠️ [Pending Discovery]"


def _or_pending(value: object, stage: str | None = None) -> str:
    """Renders a value, or a stage-tagged pending marker when it is absent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"⚠️ [Pending Stage {stage} Discovery]" if stage else PENDING
    return str(value)


def _humanise(value: str | None, stage: str | None = None) -> str:
    """Turns a pack option value into prose.

    `sa_keys_rotated` reads as "Sa keys rotated", which is ugly but honest —
    it is the value the reviewer actually chose. Inventing a prettier label
    here would mean maintaining a second vocabulary alongside the pack's.
    """
    if not value:
        return _or_pending(None, stage)
    return value.replace("_", " ").capitalize()


def render_technical_dossier(
    record: UseCaseRecord,
    skipped_stages: set[int] | None = None,
    score: TechnicalScore | None = None,
) -> str:
    """Renders the complete dossier from a record.

    `score` is injectable so the turn engine can render and persist the same
    evaluation rather than computing it twice and risking two different
    numbers in the same conversation.
    """
    score = score or score_technical(record)
    meta = record.meta
    tech = record.technical
    net = tech.network
    sec = tech.security
    ground = tech.grounding
    prop = record.proposed

    title = meta.initiative_name or "Untitled Initiative"
    status = "SCOPED" if score.key2_ready else "WORKING DRAFT"

    parts: list[str] = []

    # -- Header -------------------------------------------------------------
    parts.append(
        f"# Technical Architecture Dossier & Access Checklist: {title} [{status}]\n"
    )

    if score.blockers:
        # Above the score on purpose. This is the finding that decides the
        # project, and a reader who stops here must not miss it.
        parts.append("> [!CAUTION]")
        parts.append("> **Critical blockers found.** This initiative cannot proceed as scoped.")
        for b in score.blockers:
            parts.append(f"> - **{b.id} {b.label}:** {b.rationale}")
        parts.append("")
    elif score.unconfirmed_blockers:
        # Worded as questions, not findings. Saying "airgapped" because nobody
        # asked would invent the one fact the review exists to establish.
        parts.append("> [!IMPORTANT]")
        parts.append(
            "> **Two questions decide this review, and at least one is not yet confirmed.** "
            "Key 2 cannot be granted until both are answered."
        )
        for b in score.unconfirmed_blockers:
            parts.append(f"> - **{b.id} {b.label}** — not yet confirmed")
        parts.append("")

    parts.append(f"> **Initiative / Customer:** {title}  ")
    parts.append(f"> **Record ID:** `{meta.record_id}`  ")
    parts.append(f"> **Review date:** {date.today().isoformat()}  ")
    parts.append(
        f"> **Feasibility Score (Technical Readiness):** "
        f"{score.readiness_pct}% ({score.earned}/{score.maximum})  "
    )
    parts.append(f"> **Feasibility Profile:** **{score.feasibility_profile}**  ")
    parts.append(
        f"> **Key 2 Authorisation:** "
        f"{'**APPROVED**' if score.key2_ready else '**PENDING**'}  "
    )
    level = record.technical.capability_level
    if level is not None:
        parts.append(
            f"> **GE App Capability Level (Phase 1):** Level {level.value} — "
            f"{level.label} ({level.delivery_tier.label})  "
        )
    parts.append("")

    if score.unanswered:
        parts.append(
            f"> [!NOTE]\n"
            f"> {len(score.unanswered)} of 22 subcriteria have not been discussed "
            f"yet. They score zero, so the percentage above reflects an "
            f"incomplete review rather than a failing one.\n"
        )

    parts.append("---\n")

    # -- 1. Systems ---------------------------------------------------------
    parts.append("## 1. Systems & Data Landscape Matrix\n")
    if tech.systems:
        parts.append(
            "| System | Function | Hosting | Interface | Format | Schema | Owner |"
        )
        parts.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for s in tech.systems:
            parts.append(
                f"| {s.name} | {_or_pending(s.function, '1')} "
                f"| {_or_pending(s.hosting_location, '1')} "
                f"| {_or_pending(s.interface, '1')} "
                f"| {_or_pending(s.data_format, '1')} "
                f"| {_or_pending(s.schema_status, '1')} "
                f"| {_or_pending(s.system_owner, '1')} |"
            )
    elif tech.data_sources:
        parts.append(
            "No systems itemised in the technical review. Phase 1 named these "
            "data sources, which remain unverified technically:\n"
        )
        for src in tech.data_sources:
            parts.append(f"- {src} — ⚠️ [Pending Stage 1 Discovery]")
    else:
        parts.append("⚠️ [Pending Stage 1 Discovery]")
    parts.append(f"\n- **Data freshness & sync:** {_humanise(tech.data_freshness, '1')}")
    parts.append(f"- **Lead DBA / data custodian:** {_or_pending(prop.tech_owner, '1')}\n")

    # -- 2. Network ---------------------------------------------------------
    parts.append("## 2. Infrastructure & Network Baseline\n")
    parts.append(f"- **Hosting environments:** {_or_pending(net.hosting_environments, '2')}")
    parts.append(f"- **Transit path to Google Cloud:** {_humanise(net.transit_path, '2')}")
    parts.append(f"- **Firewall & proxy status:** {_humanise(net.firewall_proxy_status, '2')}")
    parts.append(f"- **Transit approval:** {_humanise(net.transit_blocker_status, '2')}\n")

    # -- 3. Security --------------------------------------------------------
    parts.append("## 3. Security, IAM & Data Governance Profile\n")
    parts.append(f"- **User authentication:** {_humanise(sec.user_authentication, '3')}")
    parts.append(f"- **Service authentication:** {_humanise(sec.service_authentication, '3')}")
    parts.append(f"- **IAM least privilege:** {_humanise(sec.iam_least_privilege, '3')}")
    parts.append(f"- **Data classification:** {_humanise(sec.data_classification, '3')}")
    parts.append(f"- **Residency requirements:** {_humanise(sec.residency_requirements, '3')}")
    parts.append(f"- **Cloud processing policy:** {_humanise(sec.cloud_policy_status, '3')}\n")

    # -- 4. Grounding -------------------------------------------------------
    parts.append("## 4. Data Grounding & Model Routing\n")
    if ground.acl_preservation_required is None:
        acl = "⚠️ [Pending Stage 4 Discovery]"
    elif ground.acl_preservation_required:
        acl = "Required — connector must inherit repository ACLs"
    else:
        acl = "Explicitly not required for this corpus"
    parts.append(f"- **Document-level ACLs:** {acl}")
    parts.append(f"- **Citation & provenance:** {_humanise(ground.citation_policy, '4')}")
    parts.append(f"- **Expected query volume:** {_humanise(ground.query_volume, '4')}")
    parts.append(f"- **Latency target:** {_humanise(ground.latency_sla, '4')}")
    parts.append(f"- **Recommended model profile:** {_humanise(ground.model_profile, '4')}\n")

    # -- 5. Audit matrix ----------------------------------------------------
    parts.append("## 5. 22-Subcriteria Technical Audit Matrix\n")
    parts.append("| # | Subcriterion | Verdict | Points | Finding |")
    parts.append("| :--- | :--- | :--- | :--- | :--- |")
    for dimension, rows in score.by_dimension().items():
        subtotal = sum(r.points for r in rows)
        parts.append(
            f"| **{dimension}** | | | **{subtotal}/{len(rows) * 2}** | |"
        )
        for r in rows:
            mark = {PASS: "✅ PASS", 1: "⚠️ WARN", FAIL: "❌ FAIL"}[r.points]
            note = r.rationale if r.answered else f"*{r.rationale}*"
            parts.append(f"| {r.id} | {r.label} | {mark} | {r.points} | {note} |")
    parts.append(
        f"\n**Total: {score.earned}/{score.maximum} — {score.readiness_pct}% "
        f"technical readiness.**\n"
    )

    # -- 6. Feasibility -----------------------------------------------------
    parts.append("## 6. Preliminary Feasibility & Delivery Path\n")
    parts.append(f"- **Feasibility profile:** {score.feasibility_profile}")
    parts.append(f"- **Architectural rationale:** {_feasibility_rationale(record, score)}\n")
    # The architect-level capability guidance (MCP, Cloud Run, IAM, CLI) lives
    # here; the Business Value Brief carries the plain-language version.
    if record.technical.capability_rationale:
        parts.append("### Solution guidance from the Phase 1 classification\n")
        parts.append(record.technical.capability_rationale + "\n")

    # -- 7. Access checklist ------------------------------------------------
    parts.append("## 7. Access Checklist & Sprint #1 Prerequisites\n")
    for label, done, subcriterion in _checklist(record, score):
        box = "x" if done else " "
        suffix = "" if done else f" — ⚠️ blocked on {subcriterion}"
        parts.append(f"- [{box}] **{label}**{suffix}")
    parts.append("")
    parts.append(f"- **Tech owner (Lead DBA / system owner):** {_or_pending(prop.tech_owner, '5')}")
    parts.append(f"- **Network / security lead:** {_or_pending(prop.network_security_lead, '5')}")
    parts.append(f"- **Domain SME (validation):** {_or_pending(prop.domain_sme, '5')}")
    parts.append(f"- **GCP landing zone:** {_humanise(tech.landing_zone_status, '5')}\n")

    # -- 8. Next step -------------------------------------------------------
    parts.append("---\n")
    parts.append(f"## Next step\n\n{_next_step(score)}")

    return "\n".join(parts)


def _feasibility_rationale(record: UseCaseRecord, score: TechnicalScore) -> str:
    if score.blockers:
        reasons = "; ".join(f"{b.id} {b.label}" for b in score.blockers)
        return (
            f"Blocked on {reasons}. No delivery path exists until these are "
            f"resolved or an exception is granted."
        )

    net = record.technical.network
    if net.transit_path in ("ha_vpn", "interconnect", "psc"):
        return (
            f"Hybrid transit ({_humanise(net.transit_path)}) means the platform "
            f"reaches private backends, which is outside what native managed "
            f"connectors cover. A custom agent on Cloud Run is the fit."
        )
    if score.readiness_pct >= KEY2_THRESHOLD:
        return (
            "All systems are reachable over public interfaces with documented "
            "schemas, so native managed connectors should cover this."
        )
    return (
        "Too many subcriteria are unresolved to commit to a delivery path. "
        "Clear the access checklist below and re-score."
    )


def _checklist(
    record: UseCaseRecord, score: TechnicalScore
) -> list[tuple[str, bool, str]]:
    """The prerequisites, each tied to the subcriterion that decides it.

    Derived from the score rather than written out separately, so a checklist
    item cannot claim done while its subcriterion is failing.
    """
    points = {s.id: s.points for s in score.subscores}
    return [
        ("Network topology: subnets and ingress/egress verified", points["2.1"] == PASS, "2.1"),
        ("API specs / DDL: OpenAPI definitions or schemas provided", points["1.3"] == PASS, "1.3"),
        ("Landing zone provisioned: project, VPC and base IAM active", points["5.3"] == PASS, "5.3"),
        ("Hybrid transit operational (if on-premises)", points["2.2"] == PASS, "2.2"),
        ("Service accounts & IAM: minimum-privilege roles configured", points["3.3"] == PASS, "3.3"),
        ("Cloud processing authorised by enterprise policy", points["3.5"] == PASS, "3.5"),
        ("Named technical and security leads", points["5.1"] == PASS, "5.1"),
        ("Validation SME committed for prompt and eval testing", points["5.2"] == PASS, "5.2"),
    ]


def _next_step(score: TechnicalScore) -> str:
    if score.blockers:
        return (
            "**Resolve the blockers above before any further scoping.** An "
            "airgapped system or a cloud processing ban is a governance "
            "decision, not an engineering one — it needs the enterprise "
            "architecture or risk function, not the delivery team."
        )
    if score.key2_ready:
        return (
            "**Ready for Sprint #1.** Hand this dossier to the FDE team with "
            "the access checklist above. Every item is cleared."
        )
    if score.unanswered:
        return (
            f"**Finish the review.** {len(score.unanswered)} subcriteria have "
            f"not been discussed. Reopen the stages they belong to and the "
            f"score will update."
        )
    return (
        f"**Close the gaps in the access checklist.** Readiness is "
        f"{score.readiness_pct}%; Key 2 needs {KEY2_THRESHOLD}% with no blockers."
    )
