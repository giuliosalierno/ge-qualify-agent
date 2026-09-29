#!/bin/bash
# Black-box checks of SharePoint token handling on the deployed service.
#
# Verifies the security properties introduced with per-conversation tokens:
# removed endpoints stay removed, unsigned/forged links are rejected, reflected
# input is escaped, secrets are not plain env vars, and a correctly signed link
# still renders the sign-in page. Exits non-zero on the first failure.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-vais-c-exp}"
SERVICE_NAME="${SERVICE_NAME:-ge-qualify-agent}"
REGION="${REGION:-us-central1}"

LB_IP=$(gcloud compute addresses describe "${SERVICE_NAME}-ip" --global --project="$PROJECT_ID" --format="value(address)" 2>/dev/null || true)
BASE="${BASE_URL:-${LB_IP:+https://${LB_IP}.nip.io}}"
: "${BASE:?Set BASE_URL or provision the load balancer}"
echo "Target: $BASE"

# The load balancer sits behind IAP. Pass an IAP-accepted identity token as
# IAP_TOKEN (e.g. from a service account on the IAP allowlist:
#   gcloud auth print-identity-token --impersonate-service-account=SA \
#     --audiences=<IAP OAuth client id> --include-email)
AUTH=()
if [ -n "${IAP_TOKEN:-}" ]; then AUTH=(-H "Authorization: Bearer ${IAP_TOKEN}"); fi
if curl -s -o /dev/null -D - "${AUTH[@]}" "$BASE/.well-known/agent-card.json" | grep -qi '^x-goog-iap-generated-response'; then
  echo "IAP is blocking requests. Set IAP_TOKEN (see comment above) and re-run." >&2
  exit 2
fi

PASS=0
check() {  # description, expected, actual
  if [ "$2" = "$3" ]; then
    echo "  PASS  $1"; PASS=$((PASS + 1))
  else
    echo "  FAIL  $1 (expected $2, got $3)"; exit 1
  fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$@"; }

echo "--- Removed endpoints"
c=$(code "$BASE/token");                  check "GET /token is gone"           404 "$c"
c=$(code -X POST "$BASE/token");          check "POST /token is gone"          404 "$c"
c=$(code -X POST "$BASE/auth/exchange" -H 'Content-Type: application/json' -d '{"code_or_url":"x","context_id":"x"}')
check "POST /auth/exchange is gone" 404 "$c"

echo "--- Unsigned / forged links"
c=$(code "$BASE/auth?context_id=victim");               check "/auth rejects unsigned context_id"    400 "$c"
c=$(code "$BASE/auth/status?context_id=victim");        check "/auth/status rejects unsigned"        400 "$c"
FORGED=$(printf '{"context_id":"victim"}' | base64 | tr -d '=\n')
c=$(code "$BASE/auth/callback?code=abc&state=$FORGED"); check "/auth/callback rejects forged state"  400 "$c"

echo "--- Reflected input is escaped"
body=$(curl -s "${AUTH[@]}" "$BASE/auth/callback?error=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
if grep -q '<script>alert(1)</script>' <<<"$body"; then check "error page escapes input" escaped raw; fi
check "error page escapes input" escaped escaped

echo "--- Agent card"
card=$(curl -s "${AUTH[@]}" "$BASE/.well-known/agent-card.json")
has=$(python3 -c 'import json,sys; c=json.loads(sys.stdin.read()); print("yes" if c.get("securitySchemes") or "/token" in json.dumps(c) else "no")' <<<"$card")
check "card advertises no OAuth /token" no "$has"

echo "--- Service configuration"
env_names=$(gcloud run services describe "$SERVICE_NAME" --project="$PROJECT_ID" --region="$REGION" \
  --format=json | python3 -c '
import json, sys
env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
plain = sorted(e["name"] for e in env if "value" in e)
secret = sorted(e["name"] for e in env if "valueFrom" in e)
print(",".join(plain)); print(",".join(secret))')
plain=$(sed -n 1p <<<"$env_names"); secret=$(sed -n 2p <<<"$env_names")
leak=$(grep -oE 'MS_GRAPH_CLIENT_SECRET|MS_GRAPH_REFRESH_TOKEN|OAUTH_STATE_SECRET' <<<"$plain" | head -1 || true)
check "no secrets in plain env vars" "" "$leak"
check "client secret from Secret Manager" yes "$(grep -q MS_GRAPH_CLIENT_SECRET <<<"$secret" && echo yes || echo no)"
check "state key from Secret Manager"    yes "$(grep -q OAUTH_STATE_SECRET <<<"$secret" && echo yes || echo no)"

echo "--- A correctly signed link still works"
# The key is read into this process only; it is never printed.
LINK=$(OAUTH_STATE_SECRET="$(gcloud secrets versions access latest --secret=oauth-state-secret --project="$PROJECT_ID")" \
  uv run python -c 'from qualify.connectors.oauth_state import build_signin_url; import sys; print(build_signin_url(sys.argv[1], "smoke-test-ctx"))' "$BASE")
c=$(code "$LINK"); check "/auth renders for a signed link" 200 "$c"
T="${LINK#*\?t=}"
st=$(curl -s "${AUTH[@]}" "$BASE/auth/status?t=$T")
check "/auth/status reports signed-out conversation" false "$(python3 -c 'import json,sys; print(str(json.loads(sys.stdin.read())["authenticated"]).lower())' <<<"$st")"

echo "All $PASS checks passed."
