# GE Use Case Qualification Agent-go/demos2213

**GE Use Case Qualification Agent**
* **Status:** Approved
* **Document Owner:** [giuliosalierno](https://moma.corp.google.com/person/giuliosalierno)
* **Tech Lead:** giuliosalierno@google.com
* **Demo Leads & Contributors:** giuliosalierno@google.com
* **Document Self-link:** https://docs.google.com/document/d/1nFsrfmTHBmbgb79LpRNCdEOTaOnSrWGCLgeEmswpQj8/edit?tab=t.0
* **Link to go/demos:** [go/demos/demo/2213](https://demospace.corp.goog/demo/2213)
* **Demo Design Document (DDD):** https://github.com/cloud-gtm/ge-qualification-agent/blob/main/docs/ddd-ge-qualify-agent.md
* **GitHub Repository:** https://github.com/cloud-gtm/ge-qualification-agent (`main` branch for Click-to-Deploy; `integrations` branch for optional SharePoint integration)

> **Sharing Permissions Note:** When hosting this Demo Guide in Google Docs, share with **Viewer** access to:
> * Alphabet FTEs: `alphabet@google.com`
> * Alphabet TVCs: `alphabet-extendedworkforce@google.com`
> * Google Interns: `googlers-intern@google.com`

---

## Table of Contents

1. [Argolis Set Up](#argolis-set-up)
2. [Setup](#setup)
3. [Introduction and Overview](#introduction-and-overview)
4. [Argolis Automated Deployment — "Click-To-Deploy"](#argolis-automated-deployment--click-to-deploy)
5. [Demo Walkthrough](#demo-walkthrough)
6. [Talk Track](#talk-track)
7. [Output](#output)
8. [Organization Policies](#organization-policies)
9. [Costs](#costs)
10. [Qwiklabs](#qwiklabs)
11. [Feedback](#feedback)
12. [Summary](#summary)
13. [Demo Release Notes](#demo-release-notes)

---

## Argolis Set Up

*Skip this section if you already have an active GCP Argolis account.*

### Environment: Argolis (go/argolis)
* Customer Engineers (CEs) and Technical Sellers should deploy this demo into their own **GCP Argolis** environment.
* **Click-to-Deploy** automated deployment works with Argolis Self-Managed Organizations (SMOs).
* **Don’t have an Argolis GCP Environment?**
  1. Check eligibility at `go/argolis-eligibility`.
  2. Request your environment at `go/argolis`.

### Open GCP Cloud Console
* Open [Google Cloud Console](https://console.cloud.google.com/) using a dedicated Chrome profile signed in with your Argolis credentials (`admin@<your-ldap>.altostrat.com`). See `go/argolis-tips` for best practices.

### License and Restrictions
* **Gemini Enterprise Trial & User License:** Automatically provisioned during Click-to-Deploy by `click-to-deploy/demo/scripts/ensure_ge_license.sh`, which starts the 30-day Gemini Enterprise free trial on the deployed Argolis project and assigns a Gemini Enterprise seat license to your Argolis user (`admin@<your-ldap>.altostrat.com`).
* **Demo Seed Data License:** 100% synthetic enterprise records (`Cymbal Insurance`, `Cymbal Retail`, `Cymbal Bank`) generated deterministically by `click-to-deploy/demo/seed/generate_seed.py`. Contains no customer PII or restricted third-party data and is safe for live customer demonstrations.

---

## Setup

### Demo Dataset & Storage
* **Seed Records Location:** Pre-generated JSON `UseCaseRecord` files in `click-to-deploy/demo/seed/records/` (`UC-2026-DEMO01.json`, `UC-2026-DEMO02.json`, `UC-2026-DEMO03.json`) and portfolio summaries (`portfolio/latest.json`, `portfolio/latest.md`).
* **Cloud Storage Bucket:** During deployment, Terraform creates `gs://<project-id>-ge-qualify-agent/` with object versioning enabled and uploads the 3 seed records so the agent is immediately ready for Phase 2 (Technical Review) and Phase 3 (Portfolio Prioritization) demos without requiring a prior Phase 1 run.

### Platforms & Services Used
* **Gemini Enterprise (Discovery Engine):** Host web application and native A2A + A2UI v0.9 client.
* **Cloud Run (`ge-qualify-agent`):** Serverless Python 3.13 Starlette A2A JSON-RPC SSE server streaming text and interactive A2UI v0.9 surfaces.
* **Vertex AI (`gemini-3.8-flash`):** Evidence-gated structured extraction (with verbatim quote verification) and consultative response generation.
* **Cloud Storage:** Multi-actor optimistic concurrency record store (3-way field-level merge + `if_generation_match` preconditions).

### Gemini Enterprise Demo Access — How to Log In
1. Sign in to the [Google Cloud Console](https://console.cloud.google.com/) as your Argolis user (`admin@<your-ldap>.altostrat.com`).
2. Navigate to **AI Applications (Agent Builder) → Apps** (`https://console.cloud.google.com/gen-app-builder/engines`).
3. Open the **`GE Qualification Agent Workspace`** engine (`ge-qualify-engine`).
4. Click the **Web App URL** link on the Overview / Integration page to open the Gemini Enterprise web interface, and select **Qualification Agent** from the left navigation bar.
5. **Pre-warm tip:** Two minutes before your customer session, send `hello what can you do?` (or click **Launch the agent**) once to wake the Cloud Run service from scale-to-zero.

---

## Introduction and Overview

### Overview & Purpose
Enterprise AI Centers of Excellence (CoEs) receive dozens of generative AI requests across business units, but rely on disconnected spreadsheets and repetitive workshops to evaluate ROI and technical feasibility.

The **GE Use Case Qualification Agent** embeds the **GE App Use Case Discovery Framework** natively inside Gemini Enterprise. Across three guided workflows—**Business Value Intake**, **Technical Architecture Review**, and **CoE Portfolio Prioritization**—stakeholders converse naturally while the agent populates interactive A2UI v0.9 forms, computes auditable annual hours saved, assigns Gemini Enterprise Capability Levels 1–6 with anti-overcommitment rules, evaluates 22 technical readiness subcriteria, and segments the backlog into a 4-quadrant portfolio matrix.

### Objectives & Key Features Covered
1. **Phase 1 — Business Value Intake (Business Unit Owner):**
   * 4-stage conversational intake (`1. Problem and users` → `2. Sizing the value` → `3. Systems and data` → `4. Sign-off`).
   * Evidence-gated extraction that pre-fills A2UI cards only from facts explicitly stated by the user.
   * Deterministic ROI formula (`Users × Weekly Frequency × Minutes Saved ÷ 60 × 50 weeks`) and Capability Level 1–6 recommendation grounded in the Gemini Enterprise Connector Catalog.
2. **Phase 2 — Technical Architecture Review (Solution Architect):**
   * Locks the Phase 1 business baseline in Cloud Storage and opens the **Business Value Brief** in the side panel for reference.
   * Evaluates 22 technical readiness subcriteria across 5 stages (`1. Systems & Data` → `2. Network` → `3. Security & IAM` → `4. Grounding & Models` → `5. Operational Readiness`).
   * Produces a **Technical Architecture Dossier** with a deterministic readiness score (`0–100%`), Key 2 status, and a Sprint #1 Access Checklist.
3. **Phase 3 — CoE Portfolio Prioritization (AI CoE Lead):**
   * Scores every qualified initiative on **Business Value (1–5)** and **Technical Feasibility (1–5)**.
   * Renders a ranked portfolio table, a collapsible **Scoring & Quadrant Guide**, and an interactive **2×2 Value vs. Feasibility Quadrant Matrix** (`Quick Wins`, `Strategic Bets`, `Departmental Niche`, `Deprioritized`).

### Target Audience
* Enterprise AI Center of Excellence (CoE) Leads, Chief AI / Digital Officers, Solution Architects, and Business Unit Sponsors evaluating **Gemini Enterprise**.

### Architecture Diagram
![Architecture Overview](https://raw.githubusercontent.com/giuliosalierno/ge-qualify-agent/deploy/godemos/docs/architecture-overview.png)

---

## Argolis Automated Deployment — "Click-To-Deploy"

### Option A: 1-Click Deployment via `go/demos` (Recommended)
1. Open [go/demos/demo/2213](https://demospace.corp.goog/demo/2213).
2. Click the **Click-To-Deploy** button and select your Argolis Self-Managed Organization (SMO).
3. Deployment takes approximately **12–15 minutes**. You will receive an email notification once complete, and the project will appear under the `Argolis-click-to-deploy` folder in your Argolis organization.

### Option B: Manual Terraform Deployment (CLI)
If you prefer to deploy into an existing Argolis project from Cloud Shell or your workstation:

```bash
git clone https://github.com/cloud-gtm/ge-qualification-agent.git
cd ge-qualification-agent/click-to-deploy/demo/terraform

terraform init
terraform apply \
  -var="project_id=<YOUR_ARGOLIS_PROJECT_ID>" \
  -var="project_name=<YOUR_ARGOLIS_PROJECT_NAME>" \
  -var="project_number=<YOUR_ARGOLIS_PROJECT_NUMBER>" \
  -var="org_id=<YOUR_ARGOLIS_ORG_ID>" \
  -var="gcp_account_name=admin@<YOUR_LDAP>.altostrat.com" \
  -var="deployment_service_account_name=<DEPLOYER_SA_EMAIL>" \
  -var="data_location=us-central1" \
  -var="secret_stored_project=<YOUR_ARGOLIS_PROJECT_ID>"
```

When `terraform apply` finishes, it outputs `cloud_run_url`, `records_bucket`, and `gemini_enterprise_console_url`.

---

## Demo Walkthrough

### Step 1: Launch & Workflow Orientation
1. Open the Gemini Enterprise web app and select **Qualification Agent**.
2. Click the **Launch the agent** conversation starter chip (or type `hello what can you do?`).
3. The agent renders the welcome overview and an interactive A2UI card with three workflow launchers:
   * **Qualify a new use case** (Phase 1 — Business Value Intake)
   * **Start a technical review** (Phase 2 — Technical Architecture Review)
   * **Open portfolio review** (Phase 3 — CoE Portfolio Prioritization)

![Welcome Menu](https://raw.githubusercontent.com/giuliosalierno/ge-qualify-agent/deploy/godemos/docs/screenshots/01-welcome-menu.png)

---

### Step 2: Phase 1 — Business Value Intake (Business Owner Persona)
1. Click **Qualify a new use case** (or describe a use case directly in chat).
2. **Stage 1 (Problem and users):** Enter or paste:
   ```text
   I want to qualify an agent that drafts replies to supplier invoice queries for our 30-person accounts payable team. Today AP clerks read each supplier email, look the invoice up in SAP, check the payment status and type a reply by hand. We need search capabilities over our invoices and contracts. Call it Supplier query assistant, Finance department.
   ```
   * Observe how the agent extracts `Supplier query assistant`, `AP clerks`, `30`, and `Finance department` into the Stage 1 A2UI form and updates the **Workspace** side panel (`Progress` tab) with collapsible stage expansion panels. Click **Continue**.
3. **Stage 2 (Sizing the value):** Enter:
   ```text
   Each clerk handles about 40 queries a week; each takes around 15 minutes today and the agent should save about 10 minutes per query.
   ```
   * The agent populates `40` queries/week, `15` min baseline, and `10` min saved (`10,000 hours/year` across 30 clerks). Click **Continue**.
4. **Stage 3 (Systems and data):** Enter:
   ```text
   The data lives in SAP and the shared Outlook mailbox. It is confidential financial data, and permissions must be respected.
   ```
   * Click **Continue**.
5. **Stage 4 (Sign-off):** Enter:
   ```text
   Submitted by Maria Rossi, AP operations manager. Business owner Luca Bianchi, sponsor the CFO.
   ```
   * Click **Continue**. The side panel opens the completed **Business Value Brief** (`Documents` tab) showing `10,000 hours a year`, the **Recommended approach** grounded in the Gemini Enterprise Connector Catalog, and next steps.

---

### Step 3: Phase 2 — Technical Architecture Review (Solution Architect Persona)
1. Type `technical review` (or click **Start a technical review**).
2. The agent lists all qualified opportunities awaiting technical review (including the pre-seeded `Claims intake triage` and `KYC document pre-check`, plus any newly qualified use cases).
3. Select `Claims intake triage` (or `Supplier query assistant`).
4. Notice the side panel opens **`Workspace — Claims intake triage`** with the locked **Business Value Brief** in the `Documents` tab, stage-by-stage tracking in the `Progress` tab, and the 22-point readiness rubric in the `Checklist` tab.

![Technical Architecture Review and Business Value Brief](https://raw.githubusercontent.com/giuliosalierno/ge-qualify-agent/deploy/godemos/docs/screenshots/04-technical-review-and-brief.png)

5. Walk through the 5 technical stages via chat or A2UI cards (`1. Systems & Data` → `2. Network` → `3. Security & IAM` → `4. Grounding & Models` → `5. Operational Readiness`) to generate the **Technical Architecture Dossier** and **Sprint #1 Access Checklist**.

---

### Step 4: Phase 3 — CoE Portfolio Prioritization (AI CoE Lead Persona)
1. Type `portfolio review` (or click **Open portfolio review**).
2. The side panel opens **AI CoE Portfolio Prioritization** with three tabs:
   * **`Ranked list` tab:** Displays all opportunities ranked by quadrant and composite score (`60% value, 40% feasibility`), plus the collapsible **ℹ️ Scoring & Quadrant Guide** explaining how Value (1–5), Feasibility (1–5), and Quadrant rankings are calculated.
   * **`Matrix` tab:** Renders the interactive A2UI v0.9 2×2 scatter chart plotting **Technical feasibility (1–5)** on the X-axis against **Business value (1–5)** on the Y-axis across **Quick Wins**, **Strategic Bets**, **Departmental Niche**, and **Deprioritized** quadrants, with bubble size proportional to annual hours saved.
   * **`Actions` tab:** Provides one-click buttons to open any opportunity's workspace or launch its technical review.

![Portfolio Ranked List](https://raw.githubusercontent.com/giuliosalierno/ge-qualify-agent/deploy/godemos/docs/screenshots/02-portfolio-ranked-list.png)

![Portfolio Quadrant Matrix](https://raw.githubusercontent.com/giuliosalierno/ge-qualify-agent/deploy/godemos/docs/screenshots/03-portfolio-quadrant-matrix.png)

---

## Talk Track

The talk track below corresponds to each stage of the live demo, pairing what to say with the exact action to take and the capability highlighted.

| Talk-track | Action | Area of Focus |
| :--- | :--- | :--- |
| "Every enterprise AI Center of Excellence faces the same bottleneck: business teams submit vague AI ideas in spreadsheets, and architects spend weeks chasing basic sizing and security details. Inside Gemini Enterprise, the Use Case Qualification Agent replaces that guesswork with a governed three-phase workflow across Business Owners, Solution Architects, and CoE Leads." | Click **Launch the agent** (or type `hello what can you do?`). Pause on the 3-workflow A2UI Welcome Card, then click **Qualify a new use case**. | **Intro & Native A2A + A2UI v0.9 Architecture:** Streaming interactive cards inside Gemini Enterprise alongside chat. |
| "Let's step into the shoes of Maria Rossi, an Accounts Payable manager. Instead of filling out a rigid form, Maria describes her team's pain point in plain English. Watch the A2UI card and the Workspace panel on the right: the agent extracts the initiative name, role, headcount, and manual process directly from her words—and refuses to invent anything she didn't state." | Paste: `I want to qualify an agent that drafts replies to supplier invoice queries for our 30-person accounts payable team. Today AP clerks read each supplier email, look the invoice up in SAP, check the payment status and type a reply by hand. We need search capabilities over our invoices and contracts. Call it Supplier query assistant, Finance department.` → Click **Continue**. | **Phase 1 (Stage 1) — Evidence-Gated Extraction:** Zero-hallucination form pre-fill with verbatim quote verification. |
| "Next, Maria shares her weekly task volume and expected time savings per query. Rather than relying on an LLM to guess ROI, a deterministic Python engine calculates 10,000 hours saved per year across 30 clerks over 50 work weeks." | Paste: `Each clerk handles about 40 queries a week; each takes around 15 minutes today and the agent should save about 10 minutes per query.` → Click **Continue**. | **Phase 1 (Stage 2) — Deterministic Value Sizing:** Auditable hours-saved math (`30 × 40 × 10 ÷ 60 × 50 = 10,000 hrs/yr`). |
| "Now Maria names the underlying systems—SAP and a shared Outlook mailbox—along with confidential data rules and executive sponsorship. When she submits Stage 4, the agent grounds its recommendation against the Gemini Enterprise Connector Catalog (Levels 1–6) and opens the finished Business Value Brief right in the side panel." | Stage 3: `The data lives in SAP and the shared Outlook mailbox. It is confidential financial data, and permissions must be respected.` → Click **Continue**.<br>Stage 4: `Submitted by Maria Rossi, AP operations manager. Business owner Luca Bianchi, sponsor the CFO.` → Click **Continue**. | **Phase 1 Deliverable — Business Value Brief & Anti-Overcommitment Tiering:** Matching the simplest GE capability (Levels 1–6) before committing engineering capacity. |
| "Maria's Business Value Brief is now locked in Cloud Storage using optimistic concurrency. Next, Paolo Verdi, a Solution Architect, starts a Technical Architecture Review. Notice how the side panel keeps the Business Value Brief open for reference in the Documents tab while tracking technical readiness across 5 stages and 22 subcriteria." | Type `technical review`, then select `Supplier query assistant` (or `Claims intake triage` for a 2-minute shortcut). Walk through Stages 1–5 or inspect the `Progress` and `Checklist` tabs in the Workspace panel. | **Phase 2 — Seamless Business-to-Technical Handoff & 22-Point Readiness Rubric:** Eliminating redundant discovery workshops and producing a Sprint #1 Access Checklist. |
| "Finally, the AI CoE Lead opens the Portfolio Prioritization view to rank the entire enterprise backlog. In the Ranked List tab, every initiative is scored on Business Value (1–5) and Feasibility (1–5) with a transparent Scoring & Quadrant Guide. Switching to the Matrix tab plots the entire portfolio into four quadrants—instantly separating immediate Quick Wins from Strategic Bets and blocked initiatives." | Type `portfolio review` (or click **Open portfolio review**). Show the **Ranked list** table, expand **ℹ️ Scoring & Quadrant Guide**, and switch to the **Matrix** tab. | **Phase 3 — Board-Ready CoE Portfolio Prioritization:** 2×2 Value vs. Feasibility scatter matrix and composite ranking. |

---

## Output

Running the demo produces three structured deliverables persisted in `gs://<project-id>-ge-qualify-agent/` and rendered live in the Gemini Enterprise side panel:

1. **Business Value Brief (`Documents` tab & `records/<ID>.json`):**
   * Executive summary, team & sponsor attribution, auditable annual hours saved, systems & data sensitivity, and a **Recommended approach** grounded in the Gemini Enterprise Connector Catalog (Capability Levels 1–6: No-Code Prompt/Skill → Knowledge Datastore Agent → Low-Code Workflow Agent → Pro-Code ADK / Custom MCP Agent).
2. **22-Point Technical Architecture Dossier & Sprint #1 Access Checklist (`Checklist` & `Documents` tabs):**
   * Deterministic technical readiness score (`0–100%`), Key 2 status (`Pass`, `Conditional`, or `Blocked`), per-stage subcriteria breakdown (Systems, Network/PSC, IAM/Residency, Grounding/ACLs, Operational Readiness), and named owner sign-offs required before Sprint #1.
3. **CoE Portfolio Prioritization Report (`portfolio/latest.json` & `portfolio/latest.md`):**
   * Ranked opportunity table, composite scores (`0.6 × Value + 0.4 × Feasibility`), and the 4-quadrant scatter matrix (**Quick Wins**, **Strategic Bets**, **Departmental Niche**, **Deprioritized**).
4. **Optional Microsoft SharePoint Integration (`integrations` branch):**
   * When configured with `STORAGE_PROVIDER=sharepoint`, the agent automatically publishes formatted Business Value Briefs, Technical Dossiers, and Portfolio Reports directly to enterprise SharePoint document libraries.

---

## Organization Policies

Click-to-Deploy modifies only a single Organization Policy constraint in your Argolis project (`click-to-deploy/org_policy/project_config.json`):

* **Disable Service Account Creation (`constraints/iam.disableServiceAccountCreation`):**
  * **Why modified:** Argolis enforces `constraints/iam.disableServiceAccountCreation` by default. Automation sets this boolean constraint to `Not enforced` (`enforce: false`) so Terraform can create the dedicated least-privilege runtime service account (`ge-qualify-agent-sa@<project-id>.iam.gserviceaccount.com`).
  * **No Compute Engine VM relaxations required:** Because the architecture is 100% serverless (Cloud Run + Cloud Storage + Vertex AI + Discovery Engine), `constraints/compute.vmExternalIpAccess` and `constraints/compute.requireShieldedVm` remain untouched and enforced.

---

## Costs

* **Idle Cost:** **~$0.05 / day**
  * Cloud Run is configured with `min_instance_count = 0` (scales to zero when idle).
  * Cloud Storage stores <1 MB of JSON seed records.
  * Gemini Enterprise uses the 30-day free trial activated during deployment.
* **Active Demo Cost:** **<$0.10 per complete 3-phase walkthrough**
  * Uses `gemini-3.8-flash` on Vertex AI and a single `1 vCPU / 1 GiB` Cloud Run instance during active turns.
* **Saving Costs:**
  * When you are finished with your customer engagement, delete the Click-to-Deploy project under the `Argolis-click-to-deploy` folder in [Cloud Resource Manager](https://console.cloud.google.com/cloud-resource-manager).

---

## Qwiklabs

To build deeper hands-on familiarity with the Google Cloud products featured in this demo, review the following labs on `https://ce.qwiklabs.com`:

* **Gemini Enterprise & Vertex AI Agent Builder:** *Vertex AI Search and Conversation: Qwik Start*
* **Cloud Run Serverless Deployment:** *Developing a Backend Service with Cloud Run*
* **Vertex AI Generative Models:** *Generative AI with Vertex AI: Prompt Design*

*(If you do not yet have Creator access on `https://ce.qwiklabs.com`, complete the CE Qwiklabs Onboarding course on Grow; see `go/ce-qwiklabs-acess-error` for troubleshooting).*

---

## Feedback

Comments and feedback are greatly appreciated and help us improve this demo!

1. Open [go/demos/demo/2213](https://demospace.corp.goog/demo/2213) and click the **Feedback** button.
2. Reference **GE Use Case Qualification Agent (Demo #2213)** and share your feedback on the qualification workflows, A2UI v0.9 experience, Click-to-Deploy automation, or customer reception.

---

## Summary

As demonstrated in this walkthrough, the **GE Use Case Qualification Agent** transforms Gemini Enterprise from a general-purpose assistant into a governed enterprise AI intake, architecture review, and portfolio prioritization platform. By combining conversational extraction, interactive A2UI v0.9 side-panel surfaces, deterministic ROI and 22-point technical readiness scoring, and Cloud Storage optimistic concurrency, Customer Engineers and AI CoE Leads can qualify and rank customer use cases live in under 10 minutes.

---

## Demo Release Notes

| Date | Version | Notes |
| :--- | :--- | :--- |
| Oct 12, 2026 | 1.0 | Initial `go/demos` Click-to-Deploy release (`Demo #2213`): 3-phase qualification workflow (Business Value Intake, 22-point Technical Architecture Review, 4-quadrant CoE Portfolio Prioritization), A2UI v0.9 composite catalog surfaces, automated Gemini Enterprise license provisioning, and pre-seeded Cymbal portfolio in Cloud Storage. |
