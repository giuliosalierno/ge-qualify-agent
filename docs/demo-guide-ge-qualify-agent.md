<!-- linter off -->
<!-- common_typos_disable -->

# **Demo Guide: GE Use Case Qualification Agent**

*Save the finished document as `docs/demo-guide-ge-qualify-agent.md`. Every input and sample output below is verified against the live end-to-end run on `gemini-3.8-flash` and GCS record store.*

---

## **1. Resources**

*   **Status:** APPROVED
*   **Owner:** giuliosalierno
*   **Document Self-link:** docs/demo-guide-ge-qualify-agent.md
*   **Demo Page:** docs/GE Use Case Qualification Agent_metadata.yaml (pending `go/demos` ID upon publication)
*   **Design Doc (DDD):** [docs/ddd-ge-qualify-agent.md](./ddd-ge-qualify-agent.md)
*   **Code Repository:** https://github.com/cloud-gtm/ge-qualification-agent (`deploy/godemos` branch)
*   **Demo Video:** N/A (Storyboard manifest ready at `docs/video-assets/scenes.json`)

---

## **2. Storyboard**

### **2.1. Target Audience & Goal**

*   **Target Audience:** Enterprise AI Center of Excellence (CoE) Leads, CIOs/CTOs, Solution Architects, and Business Unit Leaders evaluating Gemini Enterprise.
*   **Goal:** Showcase Gemini Enterprise as a governed AI intake and portfolio engine—demonstrating how a raw business idea is captured via conversational chat + live A2UI forms (Phase 1), handed over to a Solution Architect for a 22-point technical readiness review (Phase 2), and ranked alongside the enterprise backlog in a 4-quadrant Portfolio matrix (Phase 3).

### **2.2. Scenes**

---

**Scene 1: Welcome and Workflow Orientation**

**Voiceover:**
Every enterprise AI Center of Excellence faces the same bottleneck: dozens of business teams submit vague AI ideas in spreadsheets, and architects spend weeks chasing basic sizing and security details. Inside Gemini Enterprise, the Use Case Qualification Agent replaces that guesswork with a governed three-phase workflow across Business Owners, Solution Architects, and CoE Leads.

**Action:**
- Select `@GE Use Case Qualification Agent` from the Gemini Enterprise agent menu so the agent chip is active.
- Type `"hello what can you do?"`
- Click the **Submit** button.
- The agent responds with a welcome overview and renders the interactive A2UI welcome card with three workflow buttons: **Business Value Intake**, **Technical Architecture Review**, and **Portfolio Prioritization**.
- Pause for 3 seconds. Let the clean interface land.
- Click the **Business Value Intake** button.

**Screenshot:**
[SCREENSHOT-1: Gemini Enterprise chat showing the Welcome message and the A2UI Welcome Card with Business Value Intake, Technical Architecture Review, and Portfolio Prioritization buttons, followed by Stage 1 of 4 — Problem and users card]

---

**Scene 2: Phase 1 (Stages 1 & 2) — Problem Capture and Deterministic ROI Sizing**

**Voiceover:**
Let's step into the shoes of Maria Rossi, an Accounts Payable manager. Instead of filling out a rigid form, Maria describes her team's pain point in plain English. Watch the A2UI card on the right: the agent extracts the initiative name, role, headcount, and manual SAP steps directly from her words—and refuses to invent anything she didn't state. Next, when she shares her weekly volume and time saved per query, the agent deterministically calculates ten thousand hours saved per year.

**Action:**
- Type `"I want to qualify an agent that drafts replies to supplier invoice queries for our 30-person accounts payable team. Today AP clerks read each supplier email, look the invoice up in SAP, check the payment status and type a reply by hand. We need search capabilities over our invoices and contracts. Call it Supplier query assistant, Finance department."`
- Click the **Submit** button.
- Wait for the agent to pre-fill `Supplier query assistant`, `AP clerks`, `30`, and `Finance department` on the Stage 1 card.
- Pause for 2 seconds so the audience sees the auto-populated A2UI fields.
- Click the **Continue** button.
- Type `"Each clerk handles about 40 queries a week; each takes around 15 minutes today and the agent should save about 10 minutes per query."`
- Click the **Submit** button.
- Hold for 3 seconds on the updated Effort and Value card showing `40`, `15`, and `10` minutes saved.
- Click the **Continue** button.

**Screenshot:**
[SCREENSHOT-2: Stage 1 and Stage 2 A2UI cards auto-populated from chat with 30 AP clerks, 40 queries/week, 15 minutes baseline, and 10 minutes saved]

---

**Scene 3: Phase 1 (Stages 3 & 4) — Capability Tiering and Business Value Brief**

**Voiceover:**
Now Maria names the underlying systems—SAP and a shared Outlook mailbox—along with confidential data rules and project ownership. When she clicks Submit, the agent applies anti-overcommitment rules to map the use case to Capability Level 5—a Workflow Agent with a custom MCP server—and opens the completed Business Value Brief right in the side panel, sizing the opportunity at ten thousand hours a year.

**Action:**
- Type `"The data lives in SAP and the shared Outlook mailbox. It is confidential financial data, and permissions must be respected."`
- Click the **Submit** button.
- Wait for the Stage 3 card to select `SAP`, `Email or calendar`, `confidential`, and ACL preservation.
- Click the **Continue** button.
- Type `"Submitted by Maria Rossi, AP operations manager. Business owner Luca Bianchi, sponsor the CFO."`
- Click the **Submit** button.
- Wait for the Stage 4 Ownership card to populate.
- Click the **Continue** button.
- Hold for 4 seconds — this is the money shot for Phase 1: the side panel opens the complete **Business Value Brief** showing `10,000 hrs/yr`, `Strategic Bets`, and Capability Level 5 (`Tier 3: Pro-Code — Workflow agent with custom MCP server`).

**Screenshot:**
[SCREENSHOT-3: Completed Business Value Brief rendered in the Gemini Enterprise side panel showing 10,000 hrs/yr savings, Strategic Bets quadrant badge, and Level 5 capability tier]

---

**Scene 4: Phase 2 (Stages 1 to 3) — Solution Architect Handover, Network, and IAM**

**Voiceover:**
Maria's intake is saved to Cloud Storage. Now Paolo Verdi, a Solution Architect, opens his own chat and asks for pending technical reviews. He picks Maria's Supplier Query Assistant alongside pre-seeded Cymbal opportunities. Notice that the Phase 1 business baseline is locked at the top while the brief stays open in the side panel. Paolo walks through systems, Milan on-prem network transit over Private Service Connect, and Entra ID authentication with EU data residency.

**Action:**
- Type `"technical review"`
- Click the **Submit** button.
- The agent lists the 3 opportunities waiting for technical review (`Supplier query assistant`, `KYC document pre-check`, and `Claims intake triage`).
- Type `"Supplier query assistant"`
- Click the **Submit** button.
- Pause for 3 seconds to highlight the locked Opportunity Summary header and the Stage 1 of 5 Systems card.
- Type `"SAP S/4HANA is updated in real time and the Outlook mailbox continuously. Tech owner is Paolo Verdi from the ERP team."`
- Click the **Submit** button.
- Click the **Continue** button.
- Type `"SAP runs on-prem in our Milan data centre, Outlook is Microsoft 365. Traffic goes over Private Service Connect. We have not yet confirmed whether there is an airgap or transit blocker."`
- Click the **Submit** button.
- Click the **Continue** button.
- Type `"Users sign in with Entra ID SSO, the service uses a service account with least privilege. Data is confidential, must stay in the EU, and cloud policy allows Google Cloud."`
- Click the **Submit** button.
- Click the **Continue** button.

**Screenshot:**
[SCREENSHOT-4: Technical Architecture Review header locking the Phase 1 baseline alongside Stage 2 Network and Stage 3 Security & IAM A2UI cards]

---

**Scene 5: Phase 2 (Stages 4 & 5) — Grounding, Readiness, and the 22-Point Technical Dossier**

**Voiceover:**
In the final two stages, Paolo confirms strict document-level ACL preservation, citation provenance, a five-second latency SLA, and an in-progress Google Cloud landing zone. Submitting completes the 22-subcriteria evaluation: the agent generates the Technical Architecture Dossier with a sixty-eight percent readiness score, distinguishes unconfirmed transit questions from hard blockers, and compiles an actionable Sprint 1 Access Checklist.

**Action:**
- Type `"ACLs must be preserved, answers need citations, about 2,000 queries a day, under 5 seconds is fine. Use a balanced model."`
- Click the **Submit** button.
- Click the **Continue** button.
- Type `"Network security lead is Giulia Neri. The landing zone is in progress."`
- Click the **Submit** button.
- Click the **Continue** button.
- Hold for 4 seconds on the **Technical Architecture Dossier** in the side panel showing `readiness 68%`, `Custom Agent in GE App`, `Key 2 pending`, and the 22-subcriteria breakdown.

**Screenshot:**
[SCREENSHOT-5: Technical Architecture Dossier open in the side panel showing 68% readiness score, Custom Agent in GE App feasibility profile, and the 22-subcriteria checklist]

---

**Scene 6: Phase 3 — CoE Portfolio Prioritization Matrix**

**Voiceover:**
Finally, the AI Center of Excellence Lead types portfolio review to see the entire enterprise backlog in one view. The agent combines Maria and Paolo's newly qualified use case with the existing Cymbal portfolio—ninety-five thousand five hundred hours of annual value across six hundred and sixty users—and plots every opportunity into four quadrants. Store Policy Assistant and Claims Intake Triage stand out as immediate Quick Wins, while our SAP Supplier Query Assistant is scoped as a high-value Strategic Bet.

**Action:**
- Type `"portfolio review"`
- Click the **Submit** button.
- Wait for the **Portfolio prioritization** dashboard to open in the side panel.
- Hold for 4 seconds on the Vega-Lite 4-quadrant scatter plot (`Quick Wins`, `Strategic Bets`, `Departmental Niche`, `Deprioritized`) and the ranked opportunity table showing `4 opportunities · 95,500 hrs/yr · 660 users`.
- Scroll slowly through the portfolio table and quadrant breakdown.

**Screenshot:**
[SCREENSHOT-6: CoE Portfolio Prioritization side panel showing the 4-quadrant VegaChart scatter plot, 95,500 hrs/yr total savings across 4 opportunities, and action buttons per use case]

---

## **3. Prerequisites & Access**

*   **Demo App URL:** Deploy into your Argolis project via `go/demos` Click-to-Deploy (or run `terraform apply` in `click-to-deploy/demo/terraform/`), then open the `gemini_enterprise_console_url` Terraform output (`https://console.cloud.google.com/gen-app-builder/locations/global/engines/<engine-id>/overview?project=<project-id>`) and launch the Gemini Enterprise web app URL.
*   **How to log in:** Sign in with your Argolis user account (`admin@<your-domain>.altostrat.com`).
*   **Licenses / Reservations:** Automatically handled. The Click-to-Deploy script `click-to-deploy/demo/scripts/ensure_ge_license.sh` starts the 30-day Gemini Enterprise free trial and assigns a license seat to your Argolis user during deployment.
*   **One-time setup:** None required. Three synthetic Cymbal records (`UC-2026-DEMO01`, `UC-2026-DEMO02`, `UC-2026-DEMO03`) are seeded automatically into the Cloud Storage bucket (`<project>-ge-qualify-agent`) by Terraform.
*   **Reset between runs:** Type `qualify another` or `start a new qualification` in chat to start a fresh Business Value Intake with a new unique `UC-2026-<HEX>` ID without overwriting previous records. To reset the entire bucket back to the 3 seed records, re-run `terraform apply` (or delete non-`DEMO` files from `gs://<project>-ge-qualify-agent/records/`).
*   **Pre-warm steps:** Two minutes before presenting, open the Gemini Enterprise web app, select `@GE Use Case Qualification Agent`, and send `hello what can you do?` once to wake the Cloud Run container from scale-to-zero.

---

## **4. Demo Script**

*Quick-reference table for presenters. Every row represents a single user turn or A2UI button click verified against the live agent.*

| Step | Talk track | Input | Sample Output / Action |
|---|---|---|---|
| 1 | Introduce the AI CoE intake challenge and open the agent's workflow menu. | `hello what can you do?` | Agent displays the Welcome message and A2UI card with 3 buttons: **Business Value Intake**, **Technical Architecture Review**, and **Portfolio Prioritization**. Click **Business Value Intake**. |
| 2 | Describe Maria Rossi's Accounts Payable use case in natural language (Stage 1: Problem & Users). | `I want to qualify an agent that drafts replies to supplier invoice queries for our 30-person accounts payable team. Today AP clerks read each supplier email, look the invoice up in SAP, check the payment status and type a reply by hand. We need search capabilities over our invoices and contracts. Call it Supplier query assistant, Finance department.` | Agent extracts `Supplier query assistant`, `AP clerks`, `30`, and `Finance department` onto the Stage 1 A2UI card. Click **Continue** on the card. |
| 3 | Provide weekly task frequency and minutes saved so the agent computes annual ROI (Stage 2: Effort & Value). | `Each clerk handles about 40 queries a week; each takes around 15 minutes today and the agent should save about 10 minutes per query.` | Agent populates `40` queries/wk, `15` min baseline, and `10` min saved (`10,000 hrs/yr` across 30 clerks). Click **Continue** on the card. |
| 4 | Specify data sources, sensitivity, and ACL rules (Stage 3: Data & Systems). | `The data lives in SAP and the shared Outlook mailbox. It is confidential financial data, and permissions must be respected.` | Agent checks `SAP` and `Email or calendar`, sets classification to `confidential`, and enables ACL preservation. Click **Continue** on the card. |
| 5 | Confirm business ownership and generate the Business Value Brief (Stage 4: Ownership). | `Submitted by Maria Rossi, AP operations manager. Business owner Luca Bianchi, sponsor the CFO.` | Agent fills `Maria Rossi`, `Luca Bianchi`, and `the CFO`. Click **Continue** (Submit) on the card -> Side panel opens the **Business Value Brief** (`10,000 hrs/yr`, `Strategic Bets`, Level 5 Pro-Code MCP). |
| 6 | Switch persona to Paolo Verdi (Solution Architect) and list opportunities awaiting technical review. | `technical review` | Agent lists 3 pending opportunities: `Supplier query assistant`, `KYC document pre-check` (`UC-2026-DEMO03`), and `Claims intake triage` (`UC-2026-DEMO01`). |
| 7 | Select the newly created opportunity (or `Claims intake triage` for the 5-minute shortcut) to lock the Phase 1 baseline. | `Supplier query assistant` | Agent displays the locked Phase 1 Opportunity Summary header, opens the Business Brief in the side panel, and renders Stage 1 of 5 (**Systems and data landscape**). |
| 8 | Confirm backend freshness and technical owner (Tech Stage 1: Systems & Data). | `SAP S/4HANA is updated in real time and the Outlook mailbox continuously. Tech owner is Paolo Verdi from the ERP team.` | Agent sets data freshness to `realtime_assumed` and tech owner to `Paolo Verdi`. Click **Continue** on the card. |
| 9 | Provide hosting location and network transit path (Tech Stage 2: Infrastructure & Network). | `SAP runs on-prem in our Milan data centre, Outlook is Microsoft 365. Traffic goes over Private Service Connect. We have not yet confirmed whether there is an airgap or transit blocker.` | Agent records Milan on-prem + M365 hosting, `psc` transit path, and `unknown` (unconfirmed) transit blocker status. Click **Continue** on the card. |
| 10 | Confirm SSO identity, service auth, EU residency, and cloud policy approval (Tech Stage 3: Security & IAM). | `Users sign in with Entra ID SSO, the service uses a service account with least privilege. Data is confidential, must stay in the EU, and cloud policy allows Google Cloud.` | Agent populates `entra_id`, `read_only_scoped`, `confidential`, `eu_only`, and `authorised`. Click **Continue** on the card. |
| 11 | Set ACL enforcement, citation policy, query volume, and latency SLA (Tech Stage 4: Grounding & Models). | `ACLs must be preserved, answers need citations, about 2,000 queries a day, under 5 seconds is fine. Use a balanced model.` | Agent sets ACL preservation `true`, `strict` citations, `qps_estimated`, and `sub_10s` latency. Click **Continue** on the card. |
| 12 | Confirm network security lead and landing zone status to generate the 22-point Technical Dossier (Tech Stage 5: Operational Readiness). | `Network security lead is Giulia Neri. The landing zone is in progress.` | Agent fills `Giulia Neri` and `in_progress`. Click **Continue** (Submit) on the card -> Side panel opens the **Technical Architecture Dossier** (`readiness 68%`, `Custom Agent in GE App`, 22-subcriteria breakdown, Access Checklist). |
| 13 | Switch persona to the AI CoE Lead and rank the entire enterprise backlog. | `portfolio review` | Side panel opens the **Portfolio prioritization** view (`4 opportunities · 95,500 hrs/yr · 660 users · 1 Quick Wins`) with the 4-quadrant Vega-Lite chart and ranked table. |

### **4.1. Screenshot Capture Checklist**

*All 6 storyboard scenes have pre-rendered SVG/PNG visual previews generated in `docs/demo-guide-assets/ge-qualify-agent/` for offline viewing and slide decks; when recording or updating live Gemini Enterprise screenshots from an Argolis tenant, capture the following states:*

*   **SCREENSHOT-1:** Gemini Enterprise chat after sending `hello what can you do?` and clicking **Business Value Intake**, showing the Welcome card and Stage 1 of 4 card.
*   **SCREENSHOT-2:** Stage 1 and Stage 2 A2UI cards populated with `Supplier query assistant`, `30` AP clerks, `40` queries/week, `15` min baseline, and `10` min saved.
*   **SCREENSHOT-3:** Side panel open on the finished **Business Value Brief** showing `10,000 hrs/yr`, `Strategic Bets`, and Capability Level 5.
*   **SCREENSHOT-4:** Technical Architecture Review view showing the locked Opportunity Summary header and Stage 2/3 Network & Security A2UI cards.
*   **SCREENSHOT-5:** Side panel open on the finished **Technical Architecture Dossier** showing `readiness 68%`, `Custom Agent in GE App`, and the 22-subcriteria rubric.
*   **SCREENSHOT-6:** Side panel open on the **CoE Portfolio Prioritization** dashboard showing the 4-quadrant scatter plot and the 4-opportunity table (`95,500 hrs/yr`).

---

## **5. Sample Queries**

*Copy-pasteable commands and prompts verified against the running agent:*

*   `hello what can you do?`
*   `I want to qualify an agent that drafts replies to supplier invoice queries for our 30-person accounts payable team. Today AP clerks read each supplier email, look the invoice up in SAP, check the payment status and type a reply by hand. We need search capabilities over our invoices and contracts. Call it Supplier query assistant, Finance department.`
*   `Each clerk handles about 40 queries a week; each takes around 15 minutes today and the agent should save about 10 minutes per query.`
*   `The data lives in SAP and the shared Outlook mailbox. It is confidential financial data, and permissions must be respected.`
*   `Submitted by Maria Rossi, AP operations manager. Business owner Luca Bianchi, sponsor the CFO.`
*   `technical review`
*   `technical review Claims intake triage`
*   `technical review KYC document pre-check`
*   `SAP S/4HANA is updated in real time and the Outlook mailbox continuously. Tech owner is Paolo Verdi from the ERP team.`
*   `SAP runs on-prem in our Milan data centre, Outlook is Microsoft 365. Traffic goes over Private Service Connect. We have not yet confirmed whether there is an airgap or transit blocker.`
*   `Users sign in with Entra ID SSO, the service uses a service account with least privilege. Data is confidential, must stay in the EU, and cloud policy allows Google Cloud.`
*   `ACLs must be preserved, answers need citations, about 2,000 queries a day, under 5 seconds is fine. Use a balanced model.`
*   `Network security lead is Giulia Neri. The landing zone is in progress.`
*   `portfolio review`
*   `qualify another`
*   `help`

---

## **6. Revision History**

| **Version** | **Date** | **Changes** | **Author(s)** |
|---|---|---|---|
| 1.0 | 2026-10-09 | Initial Demo Guide and 6-scene storyboard verified against live E2E run (`e2e_transcript_4.md`) | giuliosalierno |

---
