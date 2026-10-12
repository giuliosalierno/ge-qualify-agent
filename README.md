# GE Qualify Agent

Qualifies a Gemini Enterprise use case **before** it becomes a delivery
commitment: what it is worth, whether it is technically feasible, and where it
ranks against the rest of the portfolio.

It ships in two forms:

- **Qualification agent**: an A2A agent with an interactive A2UI form,
  running on Cloud Run and registered in Gemini Enterprise. This is the main
  part of the repo (`qualify/`).
- **Two standalone skills**: `ge-intake-business` and `ge-review-tech`, for
  running the same interviews as plain Gemini Enterprise skills (`skills/`).

> **Status: early access.** Usable today and under active iteration.
> Interfaces and templates may change.

## Why this exists

Existing tooling grades ramp plans for accounts that have **already** bought
Gemini Enterprise. Nothing covered the pre-sale question: is this use case a
fit, what is it worth, and what would it take to land it? This repo fills that
gap and produces documents an FDE can pick up directly. It implements the
[GE App Use Case Discovery Framework](docs/framework.md).

---

## The qualification agent

The agent interviews a stakeholder in Gemini Enterprise chat, one stage per
turn, and fills a form next to the conversation as answers come in. Every
field it proposes is shown for the user to confirm or correct.

| Phase | Stages | Output |
| :--- | :--- | :--- |
| 1. Business intake | Problem and users · Effort and value · Data and systems · Ownership and next steps | Business Value Brief: annual hours saved, capability tier (Levels 1–6) |
| 2. Technical review | Systems and data landscape · Infrastructure and network · Security, IAM and governance · Data grounding and model routing · Operational readiness | Technical Architecture Dossier: 22-subcriteria readiness score, feasibility profile, access checklist |
| 3. Portfolio | Ranks every qualified use case by value and feasibility | Portfolio report with four quadrants |

A finished business brief can be picked up later for its technical review:
type `technical review` or `show pending documents to review`. Type `help` in
the chat for the full list of commands.

### Where results go

- **Records**: every session is saved to a GCS bucket (`QUALIFY_GCS_BUCKET`).
  Portfolio ranking and the pending-review list read from it.
- **SharePoint (optional)**: the user signs in with Microsoft through a signed,
  per-conversation link, and the brief, dossier and portfolio report are
  written to a shared folder under their own identity. Set `SIGNIN_CARD=0` to
  hide the sign-in prompt where testers have no account in the tenant; saving
  stays available by typing `save to sharepoint`.
- A Google Drive connector(WIP).

### Architecture

```mermaid
flowchart LR
    GE["Gemini Enterprise"] -- "A2A + A2UI" --> CR["Cloud Run: ge-qualify-agent"]
    Browser -- "HTTPS" --> LB["Load Balancer + IAP"] --> CR
    CR --> Gemini["Gemini on Vertex AI"]
    CR --> GCS["GCS record store"]
    CR -- "delegated token" --> SP["SharePoint (Microsoft Graph)"]
```

- **Access**: only the Gemini Enterprise and IAP service agents may invoke
  the Cloud Run service; `allUsers` is not an invoker. The app also checks
  that A2A calls come from Gemini Enterprise's service agent
  (`qualify/agent/ge_auth.py`, `A2A_AUTH_MODE`).
- **Browser path**: the Microsoft sign-in pages (`/auth`, `/auth/callback`)
  are reached through the Load Balancer, behind IAP.
- **Secrets**: the Microsoft client secret and the link-signing key live in
  Secret Manager and are mounted at deploy time.

### Code layout

| Path | Contents |
| :--- | :--- |
| `qualify/agent/` | A2A server, turn engine, Phase 1 → 2 handover, caller verification |
| `qualify/a2ui/` | Form compiler, actions, sign-in card, A2UI schema validation |
| `qualify/packs/` | Interview definitions: `business.yaml`, `tech.yaml` |
| `qualify/scoring/`, `qualify/export/` | Tier, technical and portfolio scoring; brief, dossier and report rendering |
| `qualify/connectors/` | Storage layer: SharePoint, Google Drive, token vault, signed links |
| `qualify/sinks/` | Session and record stores (local or GCS) |
| `agent/instructions.md` | System instruction; the capability grounding skill is appended to it |
| `scripts/` | Deploy, registration and Load Balancer scripts |

### Run locally

```bash
uv sync
uv run pytest -q                                  # unit tests
HOST=127.0.0.1 SHAREPOINT_MOCK=1 uv run start     # serves on http://127.0.0.1:8080
```

`SHAREPOINT_MOCK=1` writes documents to `.data/sharepoint_mock/` instead of
calling Microsoft Graph. Model access uses your `gcloud` Application Default
Credentials and the settings in `.env` (`GOOGLE_CLOUD_PROJECT`, `MODEL`, …).

### Deploy

```bash
scripts/setup_lb.sh       # once: Load Balancer, managed cert, IAP
scripts/deploy.sh         # build and deploy to Cloud Run, set invokers
scripts/register_ge.sh    # once: register the agent in Gemini Enterprise
scripts/sync_ge_agent.sh  # after agent card changes: refresh the GE registration
scripts/smoke_test_auth.sh
```

`deploy.sh` reads `.env`. Useful overrides: `A2A_AUTH_MODE=enforce|log`,
`SIGNIN_CARD=0|1`, `MAX_INSTANCES`.

### One-click demo (go/demos)

[`click-to-deploy/`](click-to-deploy/README.md) holds the Terraform that
go/demos runs to deploy the agent into an Argolis project: Cloud Run, a GCS
record store with synthetic seed data, and a Gemini Enterprise app with the
agent registered. It runs with `STORAGE_PROVIDER=none` (no SharePoint, no Load
Balancer).

---

## The standalone skills

Lightweight chat-only versions of Phase 1 and Phase 2 that run as standard Gemini Enterprise skills (one stage per turn in chat, rendering the final deliverable as markdown):

| Skill | Phase | Interview | Deliverable | Source & Package |
| :--- | :--- | :--- | :--- | :--- |
| `ge-intake-business` | 1 — Front office | 4 stages, 5 turns | Business Value Brief | [`SKILL.md`](skills/ge_intake_business/SKILL.md) · [`.zip`](https://github.com/cloud-gtm/ge-qualification-agent/releases/latest/download/ge_intake_business.zip) |
| `ge-review-tech` | 2 — Back office | 5 stages, 6 turns | Technical Architecture Dossier & Access Checklist | [`SKILL.md`](skills/ge_tech_review/SKILL.md) · [`.zip`](https://github.com/cloud-gtm/ge-qualification-agent/releases/latest/download/ge_tech_review.zip) |

### Capability levels

| Level | Capability | Tier |
| :--- | :--- | :--- |
| 1 | Default assistant | Tier 1: No-Code |
| 2 | Assistant with custom skill | Tier 1: No-Code |
| 3 | Workflow Builder — chat agent | Tier 2: Low-Code |
| 4 | Workflow Builder — workflow agent | Tier 2: Low-Code |
| 5 | Workflow agent with custom MCP server | Tier 3: Pro-Code |
| 6 | Custom high-code agent (ADK / A2A) | Tier 3: Pro-Code |

### Packaging and evaluation

```bash
python3 skills/package_skills.py   # rebuild the zips (repo root, git-ignored)
python3 skills/run_eval.py         # skill verification tests
```

---

## Documentation

| Document | What it covers |
| :--- | :--- |
| [`docs/framework.md`](docs/framework.md) | The GE App Use Case Discovery Framework; code comments cite it by line |

Design notes, implementation plans and integration findings live in a local,
git-ignored `doc/` folder and are not published.
