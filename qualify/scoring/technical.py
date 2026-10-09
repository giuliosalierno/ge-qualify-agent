"""The 22-subcriteria technical evaluation matrix (design plan §8.3).

Scores a record across five dimensions on a 3-point scale: 2 PASS, 1 WARN,
0 FAIL. Maximum 44 points. Readiness is earned/44.

Two things make this more than a sum.

**Blockers outrank the percentage.** An airgapped network (2.4) or a corporate
ban on cloud processing (3.5) ends the review whatever else scored. Without
that rule a project could reach 95% while being impossible to build, and the
scorecard would be actively misleading — worse than no scorecard.

**Unanswered is not the same as bad.** A field the reviewer never reached
scores 0 like an explicit failure, but `Subscore.answered` records the
difference so the dossier can say "not yet discussed" rather than accusing a
system of having no owner. The percentage stays honest either way: an
unfinished review genuinely is not ready.

Pure functions over the record. No I/O, no LLM, no ordering dependence — the
same record always produces the same score, which is what lets a reviewer argue
with a number instead of guessing at it.
"""

from __future__ import annotations

from dataclasses import dataclass

from qualify.schema.classification import (
    CLASSIFIED,
    MIXED,
    UNKNOWN,
    normalise_classification,
)
from qualify.schema.use_case_record import UseCaseRecord

#: Points awarded.
PASS, WARN, FAIL = 2, 1, 0

#: 22 subcriteria at 2 points each.
MAXIMUM = 44

#: Readiness at or above this, with no blockers, clears Key 2.
KEY2_THRESHOLD = 80

#: Subcriteria where a 0 is fatal regardless of the total.
BLOCKING_SUBCRITERIA = ("2.4", "3.5")


@dataclass(frozen=True)
class Subscore:
    """One row of the audit matrix."""

    id: str
    dimension: str
    label: str
    points: int
    rationale: str
    #: False when the reviewer never supplied the input. Scores 0 either way;
    #: the dossier words it differently.
    answered: bool = True

    @property
    def verdict(self) -> str:
        return {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}[self.points]


@dataclass(frozen=True)
class TechnicalScore:
    """The complete evaluation."""

    subscores: tuple[Subscore, ...]
    earned: int
    readiness_pct: int
    #: Blocking subcriteria the reviewer confirmed as failing. An airgap the
    #: customer actually described.
    blockers: tuple[Subscore, ...]
    #: Blocking subcriteria nobody has asked about yet. These are NOT blockers
    #: — reporting "airgapped" because the question was skipped would invent a
    #: finding, which is the one thing the review is forbidden to do. They
    #: still deny Key 2, because an unasked question is not a cleared one.
    unconfirmed_blockers: tuple[Subscore, ...]
    key2_ready: bool
    feasibility_profile: str
    maximum: int = MAXIMUM

    @property
    def unanswered(self) -> tuple[Subscore, ...]:
        return tuple(s for s in self.subscores if not s.answered)

    def by_dimension(self) -> dict[str, list[Subscore]]:
        """Subscores grouped for the dossier's matrix, in matrix order."""
        grouped: dict[str, list[Subscore]] = {}
        for s in self.subscores:
            grouped.setdefault(s.dimension, []).append(s)
        return grouped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graded(
    id_: str,
    dimension: str,
    label: str,
    value: str | None,
    grades: dict[str, tuple[int, str]],
    unanswered_note: str,
    unconfirmed_values: tuple[str, ...] = (),
) -> Subscore:
    """Scores a closed-vocabulary field by lookup.

    A value absent from `grades` scores FAIL rather than raising. The pack and
    this table can drift — a new option added to `tech.yaml` and not graded here
    should degrade the score, not crash a live review.

    `unconfirmed_values` are explicit answers that mean "we don't know yet"
    (the pack's "Not yet confirmed" option). They score like an empty field —
    FAIL, answered=False — so a blocking subcriterion answered that way is
    reported as unconfirmed rather than as a confirmed blocker.
    """
    if not value or value in unconfirmed_values:
        return Subscore(id_, dimension, label, FAIL, unanswered_note, answered=False)

    points, rationale = grades.get(
        value, (FAIL, f"Unrecognised value {value!r}; treated as not established.")
    )
    return Subscore(id_, dimension, label, points, rationale)


def _from_text(
    id_: str,
    dimension: str,
    label: str,
    value: str | None,
    *,
    pass_note: str,
    unanswered_note: str,
    warn_below: int = 0,
    warn_note: str = "",
) -> Subscore:
    """Scores a free-text field on presence, and optionally on substance.

    `warn_below` exists because "yes" and a paragraph are not the same answer.
    A hosting topology given as one word is a WARN: something was said, but not
    enough to plan against.
    """
    text = (value or "").strip()
    if not text:
        return Subscore(id_, dimension, label, FAIL, unanswered_note, answered=False)
    if warn_below and len(text) < warn_below:
        return Subscore(id_, dimension, label, WARN, warn_note)
    return Subscore(id_, dimension, label, PASS, pass_note)


# ---------------------------------------------------------------------------
# Dimension 1 — Systems and data
# ---------------------------------------------------------------------------

_D1 = "1. Systems & Data"


def _score_systems(record: UseCaseRecord) -> list[Subscore]:
    tech = record.technical
    systems = tech.systems
    sources = [s for s in tech.data_sources if s not in ("unknown", "other")]

    # 1.1 Repository inventory
    if systems:
        named = [s for s in systems if s.hosting_location]
        if len(named) == len(systems):
            s11 = Subscore(
                "1.1", _D1, "Repository inventory", PASS,
                f"{len(systems)} backend(s) named with hosting locations.",
            )
        else:
            s11 = Subscore(
                "1.1", _D1, "Repository inventory", WARN,
                f"{len(systems)} backend(s) listed; "
                f"{len(systems) - len(named)} without a hosting location.",
            )
    elif sources:
        s11 = Subscore(
            "1.1", _D1, "Repository inventory", WARN,
            "Phase 1 named data sources, but the technical review has not "
            "itemised them as systems.",
        )
    else:
        s11 = Subscore(
            "1.1", _D1, "Repository inventory", FAIL,
            "No backends identified.", answered=False,
        )

    # 1.2 Programmatic interface
    with_iface = [s for s in systems if s.interface]
    if systems and len(with_iface) == len(systems):
        s12 = Subscore(
            "1.2", _D1, "Programmatic interface", PASS,
            "Every system has a named interface.",
        )
    elif with_iface:
        s12 = Subscore(
            "1.2", _D1, "Programmatic interface", WARN,
            f"{len(systems) - len(with_iface)} system(s) without a named interface.",
        )
    else:
        s12 = Subscore(
            "1.2", _D1, "Programmatic interface", FAIL,
            "No programmatic interface established.", answered=False,
        )

    # 1.3 Schema documentation
    documented = [
        s for s in systems
        if s.schema_status and "undocumented" not in s.schema_status.lower()
    ]
    if systems and len(documented) == len(systems):
        s13 = Subscore(
            "1.3", _D1, "Schema documentation", PASS,
            "Schemas or API specifications available for every system.",
        )
    elif documented:
        s13 = Subscore(
            "1.3", _D1, "Schema documentation", WARN,
            f"{len(systems) - len(documented)} system(s) with undocumented schemas.",
        )
    else:
        s13 = Subscore(
            "1.3", _D1, "Schema documentation", FAIL,
            "Schema documentation not established.", answered=False,
        )

    # 1.4 Data freshness and SLA
    s14 = _graded(
        "1.4", _D1, "Data freshness & SLA", tech.data_freshness,
        {
            "sla_documented": (PASS, "Sync schedule and SLA both documented."),
            "frequency_only": (WARN, "Refresh frequency known; sync mechanism unconfirmed."),
            "realtime_assumed": (FAIL, "Real-time expected with no event stream identified."),
            "unknown": (FAIL, "Data freshness not yet discussed."),
        },
        "Data freshness not yet discussed.",
    )

    # 1.5 Designated DBA or data owner
    owner = (record.proposed.tech_owner or "").strip()
    system_owners = [s for s in systems if s.system_owner]
    if owner:
        s15 = Subscore("1.5", _D1, "Designated DBA / owner", PASS, f"Named owner: {owner}.")
    elif system_owners:
        s15 = Subscore(
            "1.5", _D1, "Designated DBA / owner", WARN,
            "System owners recorded per system, but no overall data custodian named.",
        )
    else:
        s15 = Subscore(
            "1.5", _D1, "Designated DBA / owner", FAIL,
            "No database owner assigned.", answered=False,
        )

    return [s11, s12, s13, s14, s15]


# ---------------------------------------------------------------------------
# Dimension 2 — Network transit
# ---------------------------------------------------------------------------

_D2 = "2. Network Transit"


def _score_network(record: UseCaseRecord) -> list[Subscore]:
    net = record.technical.network

    s21 = _from_text(
        "2.1", _D2, "Hosting topology", net.hosting_environments,
        warn_below=20,
        warn_note="Provider named without region or VPC detail.",
        pass_note="Cloud regions or on-premises data centres mapped.",
        unanswered_note="Hosting location unknown.",
    )

    s22 = _graded(
        "2.2", _D2, "Hybrid transit path", net.transit_path,
        {
            "ha_vpn": (PASS, "Cloud HA-VPN path identified."),
            "interconnect": (PASS, "Dedicated or Partner Interconnect identified."),
            "psc": (PASS, "Private Service Connect identified."),
            "public_https": (WARN, "Public HTTPS with IP allowlisting planned."),
            "undefined": (FAIL, "Transit path undefined."),
        },
        "Transit path undefined.",
    )

    s23 = _graded(
        "2.3", _D2, "Egress proxy & firewalls", net.firewall_proxy_status,
        {
            "cleared": (PASS, "TLS inspection and proxy ports cleared."),
            "ticket_pending": (WARN, "Corporate proxy in path; firewall ticket pending."),
            "blocked": (FAIL, "Inspecting proxy blocks gRPC or TLS."),
            "unknown": (FAIL, "Proxy and firewall posture not investigated."),
        },
        "Proxy and firewall posture not investigated.",
    )

    s24 = _graded(
        "2.4", _D2, "Airgap / transit blockers", net.transit_blocker_status,
        {
            "approved": (PASS, "Transit to Google Cloud is approved."),
            "exception_required": (WARN, "Exception process required."),
            "airgapped": (FAIL, "Airgapped system; cloud transit is barred."),
        },
        "Transit approval not confirmed.",
        unconfirmed_values=("unknown",),
    )

    return [s21, s22, s23, s24]


# ---------------------------------------------------------------------------
# Dimension 3 — Security and IAM
# ---------------------------------------------------------------------------

_D3 = "3. Security & IAM"


def _score_security(record: UseCaseRecord) -> list[Subscore]:
    sec = record.technical.security

    s31 = _graded(
        "3.1", _D3, "End-user identity", sec.user_authentication,
        {
            "workspace_sso": (PASS, "Google Workspace SSO."),
            "entra_id": (PASS, "Microsoft Entra ID."),
            "okta": (PASS, "Okta."),
            "saml": (PASS, "SAML 2.0."),
            "multiple_idp": (WARN, "Multiple IdPs requiring federation."),
            "basic_auth": (FAIL, "Unmanaged or basic authentication."),
            "unknown": (FAIL, "Identity provider not confirmed."),
        },
        "Identity provider not confirmed.",
    )

    s32 = _graded(
        "3.2", _D3, "Service authentication", sec.service_authentication,
        {
            "wif": (PASS, "Workload Identity Federation mapped."),
            "sa_keys_rotated": (WARN, "Service account keys with vault rotation."),
            "oauth_client": (WARN, "OAuth client credentials."),
            "static_tokens": (FAIL, "Hardcoded long-lived static tokens."),
            "unknown": (FAIL, "Service authentication not confirmed."),
        },
        "Service authentication not confirmed.",
    )

    s33 = _graded(
        "3.3", _D3, "IAM least privilege", sec.iam_least_privilege,
        {
            "read_only_scoped": (PASS, "Read-only, scoped service identities."),
            "broad_reader": (WARN, "Broad dataset reader role proposed."),
            "admin": (FAIL, "Admin or owner role requested."),
            "unknown": (FAIL, "IAM scope not confirmed."),
        },
        "IAM scope not confirmed.",
    )

    # 3.4 folds classification and residency: a sensitivity label with an
    # unstated residency requirement is the WARN the matrix describes.
    # Both packs write this field; normalising accepts legacy Phase 1 values
    # ("restricted") and treats "Not sure yet" as unanswered, not unclassified.
    classification = normalise_classification(sec.data_classification)
    residency = sec.residency_requirements
    if not classification or classification == UNKNOWN:
        s34 = Subscore(
            "3.4", _D3, "Data classification", FAIL,
            "Data sensitivity not classified.", answered=False,
        )
    elif classification in CLASSIFIED:
        if residency and residency != "unknown":
            s34 = Subscore(
                "3.4", _D3, "Data classification", PASS,
                f"Classified {classification}; residency requirement recorded.",
            )
        else:
            s34 = Subscore(
                "3.4", _D3, "Data classification", WARN,
                f"Classified {classification}, but residency is unconfirmed.",
            )
    elif classification == MIXED:
        s34 = Subscore(
            "3.4", _D3, "Data classification", WARN,
            "Data mix unclear; review scheduled.",
        )
    else:
        s34 = Subscore(
            "3.4", _D3, "Data classification", FAIL,
            "Unclassified; potential untagged PII or PCI.",
        )

    s35 = _graded(
        "3.5", _D3, "Cloud processing policy", sec.cloud_policy_status,
        {
            "authorised": (PASS, "Enterprise policy authorises GCP processing."),
            "review_in_flight": (WARN, "Legal or risk review in flight."),
            "banned": (FAIL, "Corporate policy bans cloud data processing."),
        },
        "Cloud processing policy not confirmed.",
        unconfirmed_values=("unknown",),
    )

    return [s31, s32, s33, s34, s35]


# ---------------------------------------------------------------------------
# Dimension 4 — Grounding and models
# ---------------------------------------------------------------------------

_D4 = "4. Grounding & Models"


def _score_grounding(record: UseCaseRecord) -> list[Subscore]:
    g = record.technical.grounding

    # 4.1 is a boolean, so "not asked" and "answered no" are different facts
    # that `None` and `False` keep apart. An explicit no is a real answer.
    if g.acl_preservation_required is None:
        s41 = Subscore(
            "4.1", _D4, "Document ACLs", FAIL,
            "ACL requirement not established.", answered=False,
        )
    elif g.acl_preservation_required:
        s41 = Subscore(
            "4.1", _D4, "Document ACLs", PASS,
            "Connector must inherit repository ACLs; requirement is explicit.",
        )
    else:
        s41 = Subscore(
            "4.1", _D4, "Document ACLs", PASS,
            "ACL preservation explicitly not required for this corpus.",
        )

    s42 = _graded(
        "4.2", _D4, "Citation & provenance", g.citation_policy,
        {
            "strict": (PASS, "Strict source attribution required and supported."),
            "loose": (WARN, "Loose attribution acceptable."),
            "none": (FAIL, "Grounding unanchored; no citation requirement."),
        },
        "Citation policy not discussed.",
    )

    # 4.3 needs both halves. Volume without a latency target is the WARN.
    volume, latency = g.query_volume, g.latency_sla
    if not volume:
        s43 = Subscore(
            "4.3", _D4, "Query volume & latency", FAIL,
            "Query volume not estimated.", answered=False,
        )
    elif volume == "unbounded":
        s43 = Subscore(
            "4.3", _D4, "Query volume & latency", FAIL,
            "Unbounded concurrency with no SLA.",
        )
    elif volume == "qps_estimated" and latency in ("sub_2s", "sub_10s"):
        s43 = Subscore(
            "4.3", _D4, "Query volume & latency", PASS,
            "Target QPS and latency SLA both mapped.",
        )
    else:
        s43 = Subscore(
            "4.3", _D4, "Query volume & latency", WARN,
            "Volume estimated without a committed latency SLA.",
        )

    s44 = _graded(
        "4.4", _D4, "Model sizing & routing", g.model_profile,
        {
            "flash": (PASS, "Gemini Flash justified for search and summarisation."),
            "pro": (PASS, "Gemini Pro justified for multi-system synthesis."),
            "mixed": (PASS, "Mixed routing by query type."),
            "undecided": (FAIL, "Model choice not made."),
        },
        "Model profile not selected.",
    )

    return [s41, s42, s43, s44]


# ---------------------------------------------------------------------------
# Dimension 5 — Operational readiness
# ---------------------------------------------------------------------------

_D5 = "5. Operational Readiness"


def _score_readiness(record: UseCaseRecord) -> list[Subscore]:
    prop = record.proposed
    tech_owner = (prop.tech_owner or "").strip()
    sec_lead = (prop.network_security_lead or "").strip()
    sme = (prop.domain_sme or "").strip()

    if tech_owner and sec_lead:
        s51 = Subscore(
            "5.1", _D5, "Technical stakeholders", PASS,
            f"Technical owner ({tech_owner}) and security lead ({sec_lead}) named.",
        )
    elif tech_owner or sec_lead:
        s51 = Subscore(
            "5.1", _D5, "Technical stakeholders", WARN,
            "Technical team identified, but one of the two leads is missing.",
        )
    else:
        s51 = Subscore(
            "5.1", _D5, "Technical stakeholders", FAIL,
            "No technical owners assigned.", answered=False,
        )

    if sme:
        s52 = Subscore("5.2", _D5, "Validation SME", PASS, f"SME committed: {sme}.")
    else:
        s52 = Subscore(
            "5.2", _D5, "Validation SME", FAIL,
            "No validation resource identified.", answered=False,
        )

    s53 = _graded(
        "5.3", _D5, "GCP landing zone", record.technical.landing_zone_status,
        {
            "provisioned": (PASS, "Project, VPC and base IAM provisioned."),
            "in_progress": (WARN, "Project requested; provisioning in progress."),
            "none": (FAIL, "No GCP footprint or billing account."),
            "unknown": (FAIL, "Landing zone status not confirmed."),
        },
        "Landing zone status not confirmed.",
    )

    # 5.4 reads the Phase 1 tier against what Phase 2 found. A Tier 1 call
    # made before anyone knew the data sat on-premises is exactly the
    # borderline case the matrix wants flagged.
    level = record.technical.capability_level
    if level is None:
        s54 = Subscore(
            "5.4", _D5, "Delivery tier fit", FAIL,
            "No delivery tier proposed in Phase 1.", answered=False,
        )
    else:
        tier = level.delivery_tier
        transit = record.technical.network.transit_path
        hybrid = transit in ("ha_vpn", "interconnect", "psc")
        if hybrid and tier.value < 3:
            s54 = Subscore(
                "5.4", _D5, "Delivery tier fit", WARN,
                f"Phase 1 proposed {tier.label}, but hybrid transit points to "
                f"pro-code delivery. Worth revisiting.",
            )
        else:
            s54 = Subscore(
                "5.4", _D5, "Delivery tier fit", PASS,
                f"{tier.label} is consistent with the technical findings.",
            )

    return [s51, s52, s53, s54]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def score_technical(record: UseCaseRecord) -> TechnicalScore:
    """Evaluates all 22 subcriteria and derives the feasibility profile."""
    subscores = tuple(
        s
        for group in (
            _score_systems(record),
            _score_network(record),
            _score_security(record),
            _score_grounding(record),
            _score_readiness(record),
        )
        for s in group
    )

    assert len(subscores) == 22, f"expected 22 subcriteria, built {len(subscores)}"

    earned = sum(s.points for s in subscores)
    readiness_pct = round(earned / MAXIMUM * 100)

    blocking = [s for s in subscores if s.id in BLOCKING_SUBCRITERIA]
    blockers = tuple(s for s in blocking if s.points == FAIL and s.answered)
    unconfirmed = tuple(s for s in blocking if not s.answered)

    # Key 2 needs the blockers affirmatively *cleared*, not merely unmentioned.
    # "Nobody asked whether the network is airgapped" is not the same as "the
    # network is not airgapped", and only the second one clears a gate.
    all_clear = all(s.points == PASS for s in blocking)
    key2_ready = readiness_pct >= KEY2_THRESHOLD and all_clear

    return TechnicalScore(
        subscores=subscores,
        earned=earned,
        readiness_pct=readiness_pct,
        blockers=blockers,
        unconfirmed_blockers=unconfirmed,
        key2_ready=key2_ready,
        feasibility_profile=_feasibility_profile(record, readiness_pct, blockers),
    )


def _feasibility_profile(
    record: UseCaseRecord, readiness_pct: int, blockers: tuple[Subscore, ...]
) -> str:
    """One of the three profiles in the SKILL's Turn 6."""
    if blockers:
        return "Blockers / High Risk"

    net = record.technical.network
    hybrid = net.transit_path in ("ha_vpn", "interconnect", "psc")
    custom_interfaces = any(
        s.interface and any(k in s.interface.lower() for k in ("jdbc", "sql", "rest", "custom"))
        for s in record.technical.systems
    )

    if hybrid or custom_interfaces:
        return "Custom Agent in GE App"
    if readiness_pct >= KEY2_THRESHOLD:
        return "Pure GE App"
    return "Custom Agent in GE App"
