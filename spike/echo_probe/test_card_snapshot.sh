#!/bin/bash
# Determines whether Gemini Enterprise uses the agent card it stored at
# registration time, or re-fetches /.well-known/agent-card.json at invoke time.
#
# Method: register a SECOND agent pointing at the same Cloud Run service, but
# with a sentinel extension injected into the stored card that the live service
# does not advertise. The probe logs `context.requested_extensions` on every
# turn, and GE builds the X-A2A-Extensions header from whichever card it trusts.
#
#   sentinel present in the header -> GE used the STORED snapshot
#   sentinel absent                -> GE re-fetched the live card
#
# Non-destructive: the original registration is untouched.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
SERVICE_NAME="${SERVICE_NAME:-a2ui-echo-probe}"
REGION="${REGION:-us-central1}"
ENGINE_ID="${ENGINE_ID:-gemini-enterprise-17888530_1788853050024}"

SENTINEL="https://example.invalid/card-snapshot-sentinel/v1"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')

curl -sf "${SERVICE_URL}/.well-known/agent-card.json" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -o /tmp/live_card.json

SENTINEL="$SENTINEL" python3 - <<'PY' > /tmp/ge_snapshot_body.json
import json, os

card = json.load(open("/tmp/live_card.json"))
card["name"] = "A2UI Card Snapshot Test"

# The sentinel exists only in the copy GE stores. The live service never
# advertises it, so seeing it come back proves GE trusted the stored card.
card["capabilities"]["extensions"].append({
    "uri": os.environ["SENTINEL"],
    "description": "Sentinel. Present only in the card registered with GE.",
})

print(json.dumps({
    "displayName": "A2UI Card Snapshot Test",
    "description": "Diagnostic. Determines whether GE uses the stored agent card or re-fetches it.",
    "a2aAgentDefinition": {"jsonAgentCard": json.dumps(card)},
}))
PY

BASE="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents"

curl -s -X POST "$BASE" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d @/tmp/ge_snapshot_body.json
echo
echo "Sentinel: $SENTINEL"
