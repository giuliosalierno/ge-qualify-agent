#!/bin/bash
# Tests the deployed ge-qualify-agent Cloud Run service.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "Testing service at: $SERVICE_URL"

TOKEN=$(gcloud auth print-identity-token)

echo "--- 1. Fetching Agent Card ---"
curl -s "${SERVICE_URL}/.well-known/agent-card.json" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -n 30
echo

echo "--- 2. Sending Test A2A Turn ---"
curl -s -X POST "${SERVICE_URL}/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-A2A-Extensions: https://a2ui.org/a2a-extension/a2ui/v0.9" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "Hello, I want to build an invoice processing assistant"}],
        "messageId": "smoke-test-1",
        "kind": "message"
      }
    }
  }' | python3 -m json.tool | head -n 40
echo
