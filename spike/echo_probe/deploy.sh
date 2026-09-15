#!/bin/bash
# Deploys the Phase 0 sendDataModel echo probe to Cloud Run.
#
# The probe is deliberately private (--no-allow-unauthenticated). Gemini
# Enterprise calls it through its own service agent, which is granted
# run.invoker separately by grant_invoker.sh.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
SERVICE_NAME="${SERVICE_NAME:-a2ui-echo-probe}"
REGION="${REGION:-us-central1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deploying '$SERVICE_NAME' to '$PROJECT_ID' / '$REGION'..."

# No MODEL or Vertex env vars: this probe never calls an LLM.
gcloud run deploy "$SERVICE_NAME" \
  --source "$SCRIPT_DIR" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --memory 512Mi \
  --no-allow-unauthenticated \
  --set-env-vars=GOOGLE_PYTHON_PACKAGE_MANAGER=uv

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.url)')

# Second pass: the agent card must advertise its own public URL, which is not
# known until the service exists.
echo "Setting AGENT_URL=$SERVICE_URL"
gcloud run services update "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --update-env-vars=AGENT_URL="$SERVICE_URL"

echo "Deployed: $SERVICE_URL"
