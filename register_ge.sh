#!/bin/bash
# Registers the ge-qualify-agent in Gemini Enterprise as an A2A agent.
#
# Prerequisite: run ./deploy.sh first so the service is deployed and IAM is configured.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"
ENGINE_ID="${ENGINE_ID:-gemini-enterprise-17888530_1788853050024}"

LB_IP=$(gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="$PROJECT_ID" --format="value(address)" 2>/dev/null || true)
if [ -n "${LB_IP}" ]; then
  SERVICE_URL="https://${LB_IP}.nip.io"
else
  SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
fi
echo "Service URL: $SERVICE_URL"

CARD_FILE="/tmp/ge_qualify_agent_card.json"
echo "Fetching agent card from ${SERVICE_URL}/.well-known/agent-card.json ..."
curl -sf "${SERVICE_URL}/.well-known/agent-card.json" \
  -o "$CARD_FILE"

echo "Agent card fetched successfully:"
cat "$CARD_FILE"
echo

PAYLOAD_FILE="/tmp/ge_register_qualify_body.json"
python3 - <<PY > "$PAYLOAD_FILE"
import json

with open("$CARD_FILE") as f:
    card_text = f.read()

payload = {
    "displayName": "GE Use Case Qualification Agent",
    "description": (
        "Qualifies business use cases for Gemini Enterprise, collects workflow parameters "
        "across a 4-stage interactive A2UI interview, scores complexity and feasibility, "
        "and guides users on the appropriate agentic capability tier."
    ),
    "a2aAgentDefinition": {
        "jsonAgentCard": card_text
    },
    "sharingConfig": {
        "scope": "ALL_USERS"
    }
}
print(json.dumps(payload, indent=2))
PY

BASE="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents"

echo "Registering agent in Gemini Enterprise..."
RESP=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST "$BASE" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d @"$PAYLOAD_FILE")

echo "$RESP"
