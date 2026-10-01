# GE Qualify Agent: go/demos Click-to-Deploy

One click deploys the qualification agent into your Argolis project and
registers it in a Gemini Enterprise app. No SharePoint, no Load Balancer, no
manual steps.

## What gets deployed

```mermaid
flowchart LR
    GE["Gemini Enterprise app"] -- "A2A + A2UI (IAM: GE service agent only)" --> CR["Cloud Run: ge-qualify-agent"]
    CR --> VAI["Gemini on Vertex AI"]
    CR --> GCS["GCS: records + synthetic seed data"]
    CR --> SM["Secret Manager: link-signing key"]
```

| Resource | Notes |
| :--- | :--- |
| Cloud Run `ge-qualify-agent` | Scales 0–1. Only the Discovery Engine service agent may invoke it. |
| Service accounts `*-run`, `*-build` | Least privilege. The Compute default SA is not used. |
| Artifact Registry + Cloud Build | Image built from this repo in your project. |
| GCS bucket `<project>-qualify-records` | Private. Holds sessions, records, and 3 synthetic use cases. |
| Gemini Enterprise app `ge-qualify-agent-app` | Created unless `ge_engine_id` is set. The agent is registered in it. |

Takes about 10–15 minutes, mostly the image build.

## Demo script (5 minutes)

All companies and people in the seed data are fictional (Cymbal).

1. Open the Gemini Enterprise app (see the `gemini_enterprise_console_url`
   output) and pick **GE Use Case Qualification Agent**.
2. **Phase 1, business intake.** Type: `I want to qualify an agent that drafts
   replies to supplier invoice queries for our 30-person AP team`. Answer the
   form one stage per turn. At the end you get a Business Value Brief with
   annual hours saved and a capability level (1–6).
3. **Phase 2, technical review.** Type `technical review`. Pick
   *Claims intake triage* (seed data) or the use case you just created.
   Five stages produce a Technical Architecture Dossier and a readiness score.
4. **Portfolio.** Type `portfolio review`. Every qualified use case is
   ranked by value and feasibility into four quadrants.

Type `help` at any time for the command list.

## Run it by hand in your own Argolis project

```bash
cd click-to-deploy/demo/terraform
terraform init
terraform apply \
  -var project_id=YOUR_PROJECT -var project_name=YOUR_PROJECT \
  -var project_number=$(gcloud projects describe YOUR_PROJECT --format='value(projectNumber)') \
  -var org_id=YOUR_ORG -var gcp_account_name=you@your-argolis-domain \
  -var deployment_service_account_name=unused -var data_location=US \
  -var secret_stored_project=unused
terraform destroy   # same -var flags; leaves the project clean
```

Requires `gcloud`, `curl` and `python3` on the machine running Terraform
(image build and Gemini Enterprise registration run as `local-exec`).

Useful variables: `ge_engine_id` (reuse an existing GE app),
`container_image` (skip the build), `model`, `seed_demo_data`,
`a2a_auth_mode`.

## Security

- No `allUsers` invoker. Cloud Run IAM admits only Gemini Enterprise's
  service agent, and the app checks the caller again (`A2A_AUTH_MODE=enforce`).
- The link-signing key is generated per deployment and stored in Secret
  Manager. Terraform state therefore contains it: keep state private.
- Synthetic data only. Do not paste customer data into a demo deployment.

## Optional: SharePoint

The full product saves documents to SharePoint under the user's own identity.
That needs an Entra app registration and a Load Balancer with IAP, so it is
out of scope for the one-click demo. See `scripts/` and the main README.

## Layout

| Path | Contents |
| :--- | :--- |
| `demo/terraform/` | Click-to-Deploy Terraform (8 mandatory go/demos variables in `variables.tf`) |
| `demo/cloudbuild.yaml` | Image build used by `build.tf` |
| `demo/scripts/register_agent.sh` | Idempotent Gemini Enterprise registration |
| `demo/seed/` | Synthetic records and their generator |
| `org_policy/project_config.json` | APIs and org policy notes for the CDP pipeline |
