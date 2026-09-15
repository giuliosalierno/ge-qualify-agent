#!/bin/bash
# Registers the deployed echo probe in Gemini Enterprise as an A2A agent.
#
# Two things have to happen for GE to reach a private Cloud Run service:
#   1. GE's Discovery Engine service agent needs roles/run.invoker.
#   2. The agent must be registered with its agent card, inlined as a JSON
#      *string* in a2aAgentDefinition.jsonAgentCard.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
SERVICE_NAME="${SERVICE_NAME:-a2ui-echo-probe}"
REGION="${REGION:-us-central1}"
ENGINE_ID="${ENGINE_ID:-gemini-enterprise-17888530_1788853050024}"

GE_SA="service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "Service URL: $SERVICE_URL"

echo "Granting run.invoker to $GE_SA ..."
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --member="serviceAccount:${GE_SA}" \
  --role=roles/run.invoker >/dev/null

echo "Fetching agent card ..."
curl -sf "${SERVICE_URL}/.well-known/agent-card.json" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -o /tmp/echo_probe_card.json
echo "Card fetched:"
cat /tmp/echo_probe_card.json
echo

# jsonAgentCard is a string field, so the card has to be serialised into it.
python3 - <<'PY' > /tmp/ge_register_body.json
import json
card = open("/tmp/echo_probe_card.json").read()
print(json.dumps({
    "displayName": "A2UI Echo Probe",
    "description": (
        "Phase 0 diagnostic. Renders a two-field A2UI form and reports whether "
        "the surface data model is echoed back in A2A message metadata."
    ),
    "a2aAgentDefinition": {"jsonAgentCard": card},
}))
PY

BASE="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents"

echo "Registering in Gemini Enterprise ..."
curl -s -X POST "$BASE" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d @/tmp/ge_register_body.json
echo
