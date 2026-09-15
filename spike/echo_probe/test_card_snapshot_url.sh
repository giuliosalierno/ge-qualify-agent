#!/bin/bash
# Decisive version of the card-freshness test.
#
# The extension sentinel was ambiguous: a client only requests extensions it
# itself implements, so an unknown URI would be dropped either way.
#
# The `url` field is not ambiguous. GE must resolve an endpoint from some card,
# and whichever card it trusts decides the path it POSTs to. So we point the
# STORED card at a path the live card never mentions and read the access log.
#
#   POST /snapshot-probe-sentinel  -> GE used the STORED snapshot (404s, fine)
#   POST /                         -> GE re-fetched the live card
#
# Same host either way, so the request still reaches our server and is logged.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
SERVICE_NAME="${SERVICE_NAME:-a2ui-echo-probe}"
REGION="${REGION:-us-central1}"
ENGINE_ID="${ENGINE_ID:-gemini-enterprise-17888530_1788853050024}"
AGENT_ID="${AGENT_ID:-12182319083050639724}"

SENTINEL_PATH="/snapshot-probe-sentinel"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')

curl -sf "${SERVICE_URL}/.well-known/agent-card.json" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -o /tmp/live_card.json

SENTINEL_PATH="$SENTINEL_PATH" python3 - <<'PY' > /tmp/ge_snapshot_patch.json
import json, os

card = json.load(open("/tmp/live_card.json"))
card["name"] = "A2UI Card Snapshot Test"
card["url"] = card["url"].rstrip("/") + os.environ["SENTINEL_PATH"]

print(json.dumps({
    "displayName": "A2UI Card Snapshot Test",
    "description": "Diagnostic. Determines whether GE uses the stored agent card or re-fetches it.",
    "a2aAgentDefinition": {"jsonAgentCard": json.dumps(card)},
}))
PY

echo "Stored card url is now:"
python3 -c "import json;print(json.loads(json.load(open('/tmp/ge_snapshot_patch.json'))['a2aAgentDefinition']['jsonAgentCard'])['url'])"

BASE="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents/${AGENT_ID}"

curl -s -X PATCH "${BASE}?updateMask=a2aAgentDefinition" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d @/tmp/ge_snapshot_patch.json | head -5
echo
echo "Now message 'A2UI Card Snapshot Test' in GE, then check the access log path."
