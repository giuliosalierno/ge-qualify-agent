#!/bin/bash
# Creates or updates the Discovery Engine `Authorization` resource that lets Gemini Enterprise
# run the Microsoft Entra ID sign-in natively inside the GE chat UI.
#
# Design note — why `tokenUri` points at THIS service and not directly at Microsoft:
#   Gemini Enterprise performs the authorization-code exchange itself against `tokenUri`. Pointing
#   it at our own `/token` endpoint means our server sees the Microsoft authorization code, swaps
#   it for the user's delegated access + refresh token, and vaults it server-side. That guarantees
#   the agent can act on behalf of the user even if GE does not forward the token on the A2A call.
#   `/token` then returns a Google OIDC token so GE still satisfies Cloud Run IAM on `POST /`.
#
#   `authorizationUri` 302-redirects straight to Microsoft, so the consent screen the user sees is
#   Microsoft's own, rendered inside the GE popup.

set -e

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
PROJECT_NUMBER="${PROJECT_NUMBER:-369594916120}"
AUTH_ID="${AUTH_ID:-sharepoint-auth}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${MS_GRAPH_CLIENT_ID:?MS_GRAPH_CLIENT_ID must be set (check .env)}"
: "${MS_GRAPH_CLIENT_SECRET:?MS_GRAPH_CLIENT_SECRET must be set (check .env)}"
: "${MS_GRAPH_TENANT_ID:?MS_GRAPH_TENANT_ID must be set (check .env)}"

LB_IP=$(gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="$PROJECT_ID" --format="value(address)" 2>/dev/null || true)
if [ -n "${LB_IP}" ]; then
  SERVICE_URL="https://${LB_IP}.nip.io"
else
  SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')
fi
SERVICE_URL="${SERVICE_URL%/}"
echo "Service URL: $SERVICE_URL"

PAYLOAD_FILE="/tmp/ge_authorization_body.json"
python3 - <<PY > "$PAYLOAD_FILE"
import json, urllib.parse

# offline_access is REQUIRED for Microsoft Entra to return a refresh token.
# prompt=consent guarantees the refresh token is (re)issued after any scope change.
scope = "https://graph.microsoft.com/Sites.ReadWrite.All offline_access openid profile"
params = {
    "client_id": "$MS_GRAPH_CLIENT_ID",
    "response_type": "code",
    "redirect_uri": "https://vertexaisearch.cloud.google.com/oauth-redirect",
    "response_mode": "query",
    "scope": scope,
    "prompt": "consent",
}
auth_uri = "$SERVICE_URL/auth?" + urllib.parse.urlencode(params)

print(json.dumps({
    "displayName": "Microsoft SharePoint (delegated user)",
    "serverSideOauth2": {
        "clientId": "$MS_GRAPH_CLIENT_ID",
        "clientSecret": "$MS_GRAPH_CLIENT_SECRET",
        "authorizationUri": auth_uri,
        "tokenUri": "$SERVICE_URL/token",
    },
}, indent=2))
PY

BASE="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/authorizations"
RESOURCE="${BASE}/${AUTH_ID}"
TOKEN=$(gcloud auth print-access-token)

if curl -sf -o /dev/null -H "Authorization: Bearer ${TOKEN}" \
     -H "x-goog-user-project: ${PROJECT_ID}" "$RESOURCE"; then
  echo "Authorization '${AUTH_ID}' exists -> PATCH"
  curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X PATCH \
    "${RESOURCE}?updateMask=serverSideOauth2,displayName" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d @"$PAYLOAD_FILE"
else
  echo "Authorization '${AUTH_ID}' not found -> CREATE"
  curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST \
    "${BASE}?authorizationId=${AUTH_ID}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d @"$PAYLOAD_FILE"
fi

echo
echo "Reminder: 'https://vertexaisearch.cloud.google.com/oauth-redirect' must be registered as a"
echo "Web redirect URI on Entra app ${MS_GRAPH_CLIENT_ID}. Run ./sync_ge_agent.sh to attach this"
echo "authorization (projects/${PROJECT_NUMBER}/locations/global/authorizations/${AUTH_ID}) to the agent."
