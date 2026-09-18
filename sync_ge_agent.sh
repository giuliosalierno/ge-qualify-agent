#!/bin/bash
# Re-syncs the live A2A Agent Card and OAuth authorization config into the already-registered
# Gemini Enterprise agent.
#
# Why this exists:
#   Gemini Enterprise stores a SNAPSHOT of the agent card taken at registration time. When the
#   card changes (for example when `securitySchemes` / `security` are added so GE renders its
#   native "Sign in" prompt), the stored snapshot goes stale and GE keeps serving the old one.
#   This script PATCHes `a2aAgentDefinition.jsonAgentCard` plus `authorizationConfig` in place.
#
# Prerequisite: run ./deploy.sh first so the newest card is live on Cloud Run.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"
ENGINE_ID="${ENGINE_ID:-gemini-enterprise-17888530_1788853050024}"
AGENT_ID="${AGENT_ID:-2763559167028725339}"
AUTH_ID="${AUTH_ID:-sharepoint-auth}"

LB_IP=$(gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="$PROJECT_ID" --format="value(address)" 2>/dev/null || true)
if [ -n "${LB_IP}" ]; then
  SERVICE_URL="https://${LB_IP}.nip.io"
else
  SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
fi
echo "Service URL: $SERVICE_URL"

CARD_FILE="/tmp/ge_qualify_agent_card.json"
echo "Fetching live agent card from ${SERVICE_URL}/.well-known/agent-card.json ..."
curl -sf "${SERVICE_URL}/.well-known/agent-card.json" \
  -o "$CARD_FILE"

python3 - <<PY
import json, sys
card = json.load(open("$CARD_FILE"))
schemes = card.get("securitySchemes") or {}
if not schemes:
    print("WARNING: live agent card has no securitySchemes; GE will not render a native sign-in prompt.", file=sys.stderr)
else:
    print("Live card declares security schemes:", ", ".join(schemes))
PY

PAYLOAD_FILE="/tmp/ge_sync_qualify_body.json"
python3 - <<PY > "$PAYLOAD_FILE"
import json

card_text = open("$CARD_FILE").read()
payload = {
    "displayName": "GE Use Case Qualification Agent",
    "description": (
        "Qualifies business use cases for Gemini Enterprise, collects workflow parameters "
        "across a 4-stage interactive A2UI interview, scores complexity and feasibility, "
        "and guides users on the appropriate agentic capability tier."
    ),
    "a2aAgentDefinition": {"jsonAgentCard": card_text},
    "authorizationConfig": {
        "toolAuthorizations": [
            "projects/$PROJECT_NUMBER/locations/global/authorizations/$AUTH_ID"
        ]
    },
}
print(json.dumps(payload, indent=2))
PY

AGENT="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents/${AGENT_ID}"
UPDATE_MASK="displayName,description,a2aAgentDefinition,authorizationConfig"

echo "Patching Gemini Enterprise agent ${AGENT_ID} (updateMask=${UPDATE_MASK}) ..."
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PATCH "${AGENT}?updateMask=${UPDATE_MASK}" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d @"$PAYLOAD_FILE"

echo
echo "Done. Start a NEW conversation in Gemini Enterprise to pick up the refreshed agent card."
