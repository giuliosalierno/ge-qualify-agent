#!/bin/bash
# Deploys the ge-qualify-agent service to Cloud Run from source.
#
# Enforces --max-instances=1 to mitigate L13 (in-memory session state)
# until Phase 3 GCS persistence is deployed.
# Grants roles/run.invoker to the Gemini Enterprise Discovery Engine service agent.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"
GENAI_LOCATION="${GENAI_LOCATION:-global}"
MODEL_NAME="${MODEL_NAME:-gemini-3-flash-preview}"
MEMORY="${MEMORY:-1Gi}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================"
echo "Deploying '$SERVICE_NAME' to '$PROJECT_ID' / '$REGION'"
echo "Model: $MODEL_NAME ($GENAI_LOCATION) | Max Instances: $MAX_INSTANCES (L13 guard)"
echo "============================================================"

# Initial deployment from source
gcloud run deploy "$SERVICE_NAME" \
  --source "$SCRIPT_DIR" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --memory "$MEMORY" \
  --max-instances "$MAX_INSTANCES" \
  --no-allow-unauthenticated \
  --set-env-vars=GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$GENAI_LOCATION",GOOGLE_GENAI_USE_VERTEXAI=TRUE,MODEL="$MODEL_NAME",GOOGLE_PYTHON_PACKAGE_MANAGER=uv

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.url)')

echo "Service deployed at: $SERVICE_URL"

# Second pass: set AGENT_URL so the agent card advertises its public endpoint
echo "Updating AGENT_URL=$SERVICE_URL ..."
gcloud run services update "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --update-env-vars=AGENT_URL="$SERVICE_URL"

# Grant roles/run.invoker to the Discovery Engine service agent
GE_SA="service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
echo "Granting roles/run.invoker to $GE_SA ..."
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --member="serviceAccount:${GE_SA}" \
  --role=roles/run.invoker >/dev/null

echo "============================================================"
echo "Deployment & IAM Setup Complete!"
echo "Service URL: $SERVICE_URL"
echo "Agent Card:  ${SERVICE_URL}/.well-known/agent-card.json"
echo "============================================================"
