# GE App Use Case Discovery Framework

This document outlines the Gemini Enterprise App (GE App) Use Case Discovery Framework. It is designed to help organizations maximize the business value of GE App through a structured, repeatable methodology that:

* **Is replicable** from one domain (department or business unit) to another;  
* **Coordinates multi-stakeholder inputs** across Central IT, business leaders, and end users, supported by the customer's Center of Excellence (CoE), Google experts (AI Customer Engineers, Forward-Deployed Engineers), and Google Cloud partners;  
* **Leverages the full spectrum of GE App capabilities**, from out-of-the-box assistants to complex, customized agentic systems;  
* **Prioritizes potential use cases** on the basis of their business value and technical feasibility;  
* **Enables progressive and consistent data collection** across all adoption phases (initial evaluation, onboarding, pilot, and full enterprise rollout) to avoid asking stakeholders to repeat themselves;  
* **Applies agentic assistance natively** to help end users articulate their needs and to support portfolio analysis at scale.

# Activities Covered by the Framework

The framework is organized into foundational setup workshops followed by five core activities:

- **Activity 0a**: Setting the Scene with Central IT (Organization-wide)  
- **Activity 0b**: Setting the Scene with Business Leaders (Domain-specific)  
- **Activity 1**: Collecting Use Case Ideas  
- **Activity 2**: Qualifying Use Case Ideas and providing no-code guidance  
- **Activity 3**: Prioritizing Use Case Ideas  
- **Activity 4**: Scoping High-Code Agents  
- **Activity 5**: Assessing the Impact of New GE App Features

In this framework, the **"CoE"** refers to the customer team in charge of facilitating the evaluation, governance, and adoption of GE App. Google experts (AI Customer Engineers, Forward-Deployed Engineers) and Google Cloud partners assist the CoE throughout these activities.

## Activity 0a: Setting the Scene with Central IT

* **Scope**: Organization-wide (conducted once at kickoff, refreshed periodically).  
* **Outline**: The CoE facilitates a workshop with Central IT and Security to establish technical environment parameters, governance guardrails, and connector availability before engaging end users.  
* **Prerequisites**: Engagement kickoff with the executive sponsor, identification of Central IT leads.  
* **Outputs**:  
  * Catalog of connectors and MCP servers (with availability dates);  
  * Defined IT constraints, identity federation guidelines, and security/data classifications (PII, confidential data);  
  * License allocation and user rollout schedule across departments.  
* **Associated Tool**: Use Case Inventory Template (Environment & Connector Catalog tab).

## Activity 0b: Setting the Scene with Business Leaders

* **Scope**: Domain-specific (repeated for each department or business unit).  
* **Outline**: For a given domain, the CoE facilitates a workshop with business leaders and domain champions to understand strategic business priorities, key operational challenges, and target workflows.  
* **Prerequisites**: Activity 0a completed, executive sponsorship confirmed, target deployment schedule established for the domain.  
* **Outputs**:  
  * Documented business context and strategic priorities for the target domain;  
  * Inventory of domain-specific data sources and systems of record;  
  * Selection of representative end users and domain experts to participate in discovery sessions.  
* **Associated Tool**: Use Case Inventory Template (Domain Context tab).

## Activity 1: Collecting Use Case Ideas

* **Scope**: Domain-specific.  
* **Outline**: For a given domain, the CoE facilitates an interactive workshop with representative end users to introduce GE App capabilities, identify friction points, and have users interact with the **Discovery Agent** to structure their ideas.  
* **Prerequisites**: Activities 0a and 0b completed for the target domain; Discovery Agent customized with the active connectors and business context from Activities 0a and 0b.  
* **Outputs**:  
  * Structured use case records submitted directly to the inventory;  
  * Initial descriptions of user pain points, workflow steps, execution frequency, and systems touched.  
* **Associated Tools**: Discovery Agent (End-User Facing), Use Case Inventory Template.

## Activity 2: Qualifying Use Case Ideas

* **Scope**: Per use case idea.  
* **Outline**: During or immediately following the intake conversation, the **Discovery Agent** evaluates whether the proposed idea is a fit for GE App. For valid use cases, it maps the requirement to the least complex agentic capability (default assistant, assistant with custom skills, Workflow Builder, Gemini Spark, Workflow Builder with custom MCP, high-code agent).  
* **Prerequisites**: Use case idea captured and refined through the Discovery Agent interview.  
* **Outputs**:  
  * Fit confirmation (or redirection rationale for non-GE App workloads);  
  * Assigned agentic capability / implementation tier;  
  * Immediate, step-by-step guidance provided to the user to configure the no-code agent (or instructions to build a simplified no-code prototype if the use case requires high-code).  
* **Associated Tools**: Discovery Agent (End-User Facing), Use Case Inventory Template.

## Activity 3: Prioritizing Use Case Ideas

* **Scope**: Portfolio-level across business units.  
* **Outline**: The CoE evaluates and ranks the submitted use cases with the assistance of the **Analysis Agent**, generating scores for **Business Value** (reach, time saved, strategic alignment) and **Technical Feasibility** (connector availability, tier complexity, data readiness, compliance).  
* **Prerequisites**: Qualified use case records in the inventory.  
* **Outputs**:  
  * Business Value (1–5) and Feasibility (1–5) scores for each idea;  
  * Portfolio segmentation into actionable quadrants: *Quick Wins* (immediate no-code ROI), *Strategic Bets* (high-value high-code builds), *Departmental Niche*, and *Deprioritized*;  
* **Associated Tools**: Analysis Agent (CoE Facing), Use Case Inventory Template.

## Activity 4: Scoping High-Code Agents

* **Scope**: Selected high-priority high-code use cases.  
* **Outline**: The CoE facilitates a technical design workshop bringing together business stakeholders and technical experts to scope production architecture and co-build requirements.  
* **Prerequisites**: Use case qualified as high-code and prioritized by the CoE and business sponsor; simplified no-code prototype created where applicable.  
* **Outputs**:  
  * Scoping document  
* **Associated Tool**: High-Code Scoping Template.

## Activity 5: Assessing the Impact of New GE App Features

* **Scope**: Continuous / Quarterly review.  
* **Outline**: As new GE App features, models, connectors, or governance capabilities are released by Google, the CoE uses the **Analysis Agent** to re-evaluate the use case backlog.  
* **Prerequisites**: Updated GE App feature release notes; active Master Use Case Inventory.  
* **Outputs**:  
  * Re-activated use cases that were previously blocked by technical or connector limitations;  
  * Modernization opportunities to migrate custom MCP servers or high-code agents to native no-code GE App features, reducing technical debt.  
* **Associated Tools**: Analysis Agent (CoE Facing), Use Case Inventory Template.

# Operational Framework Toolkit

## Discovery Agent (End-User Facing)

The Discovery Agent is a lightweight conversational agent deployed natively in the customer's Gemini Enterprise App instance.

* **Modular Skill Architecture**:  
  * skill-ge-capabilities: Describes the capabilities of GE App (Chat, Skills, Agent Designer, Spark, Connectors), enabling the agent to determine which GE App capability is adequate for a given use case.  
  * skill-customer-context: Contains the customer's business priorities, authorized data sources, active connectors, and corporate IT boundaries.  
  * skill-structured-interview: Guides the agent through a multi-turn interview to extract problem statements, workflow steps, system dependencies, volume, and frequency.  
  * skill-agent-creation-guidance: Enables the agent to provide comprehensive step-by-step instructions to create a no-code agent  
* **Intake & Creation Guidance Execution**:  
  * Conducts the multi-turn discovery interview and presents a validated summary back to the user.  
  * For immediate citizen builder opportunities (Tiers 1–4), provides comprehensive step-by-step instructions to create the no-code agent (e.g., prompt outline for Skills, canvas wiring for Agent Designer, or task triggers for Gemini Spark).  
  * Automatically formats the structured data payload and submits it to the Use Case Inventory (via Google Forms / API).

## Use Case Inventory Template

The Use Case Inventory is a Google Sheet listing all the use cases identified so far. For each use case submission, several pieces of information are progressively collected from end users or filled out by the CoE, for example:

* **Metadata**, e.g.:  
  * Submission date 🟩  
  * Submitter 🟩  
  * Department/BU 🟩  
  * Category 🟦  
* **Business needs**, e.g.:  
  * Profile of potential users 🟩  
  * Number of potential users 🟩  
  * Problem description 🟩  
  * User stories 🟩  
  * Expected impacts 🟩  
* **Technical aspects**, e.g.:  
  * Data sources and integrations needed 🟩  
  * GE App agentic capabilities needed 🟩  
* **Scoring and qualification**, e.g.:  
  * Feasibility score 🟦  
  * Business value score 🟦  
  * Scoping document 🟦  
  * Priority status (backlog, qualified, scoped, being implemented, production) 🟦  
* **Execution**, e.g.:  
  * Tech owner 🟦  
  * Business owner 🟦  
  * Executive sponsor 🟦  
  * Target MVP date 🟦  
  * Production catcher team 🟦

🟩 Information collected from end users with the Discovery Agent

🟦 Information provided by the CoE with the Analysis Agent

## Analysis Agent (CoE Facing)

A unified analysis and management agent used by the CoE to evaluate incoming ideas, score, deduplicate and categorize potential use cases, and track platform evolutions.

**Core Functions:**

* Value & Feasibility Scoring Engine: Computes independent Business Value (1–5) and Feasibility (1–5) scores  
* Portfolio Reviews & Thematic Clustering: Categorizes use case ideas and identifies duplicate or related use cases across business units to consolidate into shared enterprise agents.  
* Platform Feature Evolution Assessment: Periodically scans backlogged and Tier 5–7 use cases against new GE App releases to identify use cases that can now be simplified or migrated to no-code.

## High-Code Scoping Template

A document outlining the purpose, success criteria, technical architecture, and project plan for the implementation of an agentic use case. It includes the following sections:

* **Context and challenges**  
  * Overall context  
  * Problem statement  
  * Current situation  
* **Target solution**  
  * Objectives  
  * Project scope  
  * Technical architecture  
* **Expected impacts**  
  * Expected benefits  
  * Success criteria  
  * Risks and mitigation measures  
* **Project plan**  
  * Prerequisites  
  * Timeline  
  * Project stakeholders  
  * Project governance  
  * Handover

# 

&nbsp;