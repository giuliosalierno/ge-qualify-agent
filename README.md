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

## Design principles

- **Zero speculation.** The skills never infer pain points, systems, network
  topologies or metrics. Unknown stays unknown and visibly marked.
- **No fabricated numbers.** Missing sizing inputs are recorded as
  `[Pending Customer Input]`, never estimated.
- **Clean phase separation.** Business scope stops at hours saved and tiering;
  network, IAM and schemas belong to phase 2.
- **Blockers surface early.** Airgapped environments and enterprise bans on
  cloud processing are flagged the moment they appear.
- **Self-contained.** Each skill is a single `SKILL.md` with no external scripts
  or filesystem dependencies.

## Known limits

A Gemini Enterprise skill generates output — it cannot gate or enforce
anything. There is deliberately no sign-off, register or identity layer between
the business and technical phases; that is deferred to a phase-2 custom agent.
Treat the deliverables as evidence for a human decision, not as an approval.

## Installation

### 1. Download Skills
Download the pre-packaged zip files from the [GitHub Releases](https://github.com/giuliosalierno/ge-qualify-skills/releases) page:
* `ge_intake_business.zip` — Front office business value qualification
* `ge_tech_review.zip` — Back office technical review & security architecture

### 2. Upload to Gemini Enterprise
1. Open Gemini Enterprise.
2. Navigate to your agent settings → **Skills** → **Add Skill**.
3. Upload `ge_intake_business.zip` and `ge_tech_review.zip`.

### 3. Usage
* Trigger business intake: `/ge-intake-business [initiative description]`
* Trigger technical review: `/ge-review-tech [initiative or Business Value Brief]`

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