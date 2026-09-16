# Gemini Enterprise Opportunity Qualification Skills

Two Gemini Enterprise skills that qualify an opportunity **before** it becomes a
delivery commitment: one for business value, one for technical feasibility.

> **Status: early access.** Both skills are usable today and under active
> iteration. Interfaces and templates may change.

## Why this exists

Existing tooling grades ramp plans for accounts that have **already** bought
Gemini Enterprise. Nothing covered the pre-sale question: is this use case a
fit, what is it worth, and what would it take to land it? These two skills fill
that gap and produce documents an FDE can pick up directly.

## The two skills

| Skill | Phase | Interview | Deliverable |
| :--- | :--- | :--- | :--- |
| `ge-intake-business` | 1 — Front office | 4 stages, 5 turns | Business Value Brief |
| `ge-review-tech` | 2 — Back office | 5 stages, 6 turns | Technical Architecture Dossier & Access Checklist |

### `ge-intake-business` — business intake and value qualification

1. **Business needs & user stories** — problem description, as-is workflow, user
   personas and BU, data sources and integrations needed.
2. **Value realization & hours-saved sizing** — users (U), weekly task frequency
   (T), baseline minutes per task (M), annualized at a 60% acceleration
   benchmark over 50 work weeks.
3. **GE App agentic capabilities needed** — Three-Tier Delivery Ladder fit:
   Tier 1 out-of-the-box, Tier 2 low-code, Tier 3 pro-code.
4. **Execution & sponsorship** — business owner, executive sponsor, production
   catcher team, primary adoption KPI.

Output: a Business Value Brief marked **Qualified**, ready for phase 2.

### `ge-review-tech` — technical architecture and security review

1. **Systems & data landscape** — repositories, programmatic interfaces, schema
   readiness, data freshness.
2. **Infrastructure & network baseline** — hosting, transit path to Google Cloud
   (HA-VPN, Interconnect, PSC, public HTTPS), firewall and proxy constraints.
3. **Security, IAM least-privilege & governance** — user and service auth, data
   classification, residency, cloud processing policy.
4. **Data grounding security & model routing** — document-level ACL
   preservation, citation provenance, Flash vs Pro model profile.
5. **Operational readiness & prerequisites** — named technical owners, GCP
   landing zone status, Sprint #1 prerequisites.

Output: a Technical Architecture Dossier with a Technical Readiness Score
(0–100%), a Feasibility Profile of **Pure GE App**, **Custom Agent in GE App**
or **Blockers / High Risk**, and an Access Checklist.

## How they work

Both run as a consultative interview, **one stage per turn**. On turn 1 the
skill transfers a working draft to the Canvas agent and immediately starts
questioning — every unverified field stays a literal `⚠️ [Pending Stage X
Discovery]` placeholder until the customer confirms it. The final synthesis
turn replaces every placeholder with verified facts.

Run them in order. The Business Value Brief from phase 1 is the input to
phase 2.

## Getting started

- [`ge-intake-business`](https://github.com/giuliosalierno/ge-qualify-skills/releases/latest/download/ge_intake_business.zip) ([source](skills/ge_intake_business/SKILL.md))
- [`ge-review-tech`](https://github.com/giuliosalierno/ge-qualify-skills/releases/latest/download/ge_tech_review.zip) ([source](skills/ge_tech_review/SKILL.md))

Invoke either skill in Gemini Enterprise and describe the workflow. Connect a datasource to bring your context whenever you have one ready.

---

## Development & Packaging

To rebuild the distributable zip files from source:

```bash
python3 tests/package_skills.py
```

To run the verification tests:

```bash
python3 tests/run_eval.py
```

---

## Documentation

| Document | What it covers |
| :--- | :--- |
| [`doc/a2ui_integration.md`](doc/a2ui_integration.md) | A2UI wire format, component catalog, and **every verified Gemini Enterprise behaviour** — including the task states that silently break server-dispatched buttons |
| [`doc/sharepoint_auth.md`](doc/sharepoint_auth.md) | Per-user (on-behalf-of) SharePoint OAuth: the flow, the traps, and why GE withholding the token is spec-mandated |
| [`doc/technical_review.md`](doc/technical_review.md) | The Phase 2 pack: starting a review from a business record, the 22-subcriteria score, and why the systems inventory is extracted rather than typed |
| [`doc/design_plan.md`](doc/design_plan.md) | Qualification model, stages, and scoring |
| [`doc/implementation_plan.md`](doc/implementation_plan.md) | Build phases and decisions |
| [`doc/skill_best_practices.md`](doc/skill_best_practices.md) | Authoring guidance for the two skills |

> [!TIP]
> Read §8 of `a2ui_integration.md` before changing anything that touches the A2A
> protocol boundary. Several of its findings cost days to discover and produce no
> error message when violated — a broken agent looks identical to a working one from
> the unit tests.