#!/bin/bash
# Re-syncs the live A2A Agent Card into the already-registered Gemini Enterprise agent, and
# CLEARS any previously attached `authorizationConfig`.
#
# Why the authorization is cleared: Gemini Enterprise never forwarded the delegated Microsoft
# token over A2A (verified in Cloud Run logs), so the GE-native OAuth flow and its `/token`
# endpoint carried no user identity. `/token` was removed; SharePoint sign-in now uses a signed,
# per-conversation link rendered by the agent itself.
#
# Why this exists:
#   Gemini Enterprise stores a SNAPSHOT of the agent card taken at registration time. When the
#   card changes (for example when `securitySchemes` / `security` are added so GE renders its
#   native "Sign in" prompt), the stored snapshot goes stale and GE keeps serving the old one.
#   This script PATCHes `a2aAgentDefinition.jsonAgentCard` in place.
#
# Prerequisite: run scripts/deploy.sh first so the newest card is live on Cloud Run.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"
ENGINE_ID="${ENGINE_ID:-gemini-enterprise-17888530_1788853050024}"
AGENT_ID="${AGENT_ID:-2763559167028725339}"

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
# IAP fronts the load balancer and answers anonymous requests with a 302, so
# fall back to building the identical card from source (same function the
# server uses to serve it).
if ! curl -sf "${SERVICE_URL}/.well-known/agent-card.json" -o "$CARD_FILE" \
    || ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$CARD_FILE" 2>/dev/null; then
  # The card `url` is what GE calls for A2A. Keep it on the Cloud Run URL GE
  # already uses; the LB URL is behind IAP.
  CARD_URL="${CARD_URL:-$(gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')}"
  echo "Live card not reachable (IAP?); building it from source for ${CARD_URL} ..."
  (cd "$(dirname "${BASH_SOURCE[0]}")/.." && uv run python -c '
import sys
from qualify.agent.card import build_agent_card
print(build_agent_card(sys.argv[1]).model_dump_json(by_alias=True, exclude_none=True))
' "$CARD_URL") > "$CARD_FILE"
fi

python3 - <<PY
import json, sys
card = json.load(open("$CARD_FILE"))
if card.get("securitySchemes"):
    print("WARNING: live card still declares securitySchemes; deploy the latest build first.", file=sys.stderr)
PY

PAYLOAD_FILE="/tmp/ge_sync_qualify_body.json"
python3 - <<PY > "$PAYLOAD_FILE"
import json

card_text = open("$CARD_FILE").read()
payload = {
    "displayName": "GE Qualification Agent",
    "description": (
        "Qualifies business use cases for Gemini Enterprise, collects workflow parameters "
        "across a 4-stage interactive A2UI interview, scores complexity and feasibility, "
        "and guides users on the appropriate agentic capability tier."
    ),
    "a2aAgentDefinition": {"jsonAgentCard": card_text},
    # authorizationConfig is intentionally omitted while listed in UPDATE_MASK,
    # which clears the old GE-native OAuth binding.
    "sharingConfig": {
        "scope": "ALL_USERS"
    },
}
print(json.dumps(payload, indent=2))
PY

AGENT="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents/${AGENT_ID}"
UPDATE_MASK="displayName,description,a2aAgentDefinition,authorizationConfig,sharingConfig"

echo "Patching Gemini Enterprise agent ${AGENT_ID} (updateMask=${UPDATE_MASK}) ..."
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PATCH "${AGENT}?updateMask=${UPDATE_MASK}" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d @"$PAYLOAD_FILE"

echo
echo "Done. Start a NEW conversation in Gemini Enterprise to pick up the refreshed agent card."
