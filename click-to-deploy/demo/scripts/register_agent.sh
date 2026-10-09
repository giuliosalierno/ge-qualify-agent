#!/bin/bash
# Registers (or refreshes) the qualification agent in a Gemini Enterprise app.
#
# Idempotent: looks the agent up by display name and card URL and PATCHes it
# if present, otherwise creates it. Safe to re-run after every deploy; it is how GE's
# stored snapshot of the agent card is kept current.
#
# Required env: PROJECT_ID, ENGINE_ID, AGENT_URL, DISPLAY_NAME.
# Optional env: ACCESS_TOKEN (OAuth token for the Discovery Engine API).
# Called by click-to-deploy/demo/terraform/gemini_enterprise.tf.

set -euo pipefail

: "${PROJECT_ID:?}" "${ENGINE_ID:?}" "${AGENT_URL:?}" "${DISPLAY_NAME:?}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- 1. Fetch the live agent card ------------------------------------------
# Cloud Run IAM protects the service. Service accounts can mint a token for
# the service URL; user accounts cannot pass --audiences, so fall back.
TOKEN="$(gcloud auth print-identity-token --audiences="$AGENT_URL" 2>/dev/null \
  || gcloud auth print-identity-token)"

CARD="$WORK/card.json"
CURL=(curl -s --connect-timeout 10 --max-time 60)
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if "${CURL[@]}" -f -H "Authorization: Bearer $TOKEN" \
      "$AGENT_URL/.well-known/agent-card.json" -o "$CARD"; then
    break
  fi
  echo "Agent card not reachable yet (attempt $attempt); retrying in 15s ..."
  sleep 15
done
if [ ! -s "$CARD" ]; then
  echo "Agent card unreachable at $AGENT_URL/.well-known/agent-card.json." >&2
  echo "Check that the deploying identity may invoke the Cloud Run service." >&2
  exit 1
fi
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$CARD"

# --- 2. Build the request body ---------------------------------------------
BODY="$WORK/body.json"
python3 - "$CARD" "$DISPLAY_NAME" > "$BODY" <<'PY'
import json, sys
card_path, display_name = sys.argv[1], sys.argv[2]
print(json.dumps({
    "displayName": display_name,
    "description": (
        "Qualifies Gemini Enterprise use cases: an interactive A2UI interview "
        "that sizes business value, reviews technical feasibility and ranks "
        "the portfolio."
    ),
    "a2aAgentDefinition": {"jsonAgentCard": open(card_path).read()},
    "sharingConfig": {"scope": "ALL_USERS"},
}))
PY

# --- 3. Create or update ----------------------------------------------------
BASE="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant/agents"
# Terraform passes the runner's token (google_client_config); fall back to gcloud.
ACCESS="${ACCESS_TOKEN:-$(gcloud auth print-access-token)}"
H=(-H "Authorization: Bearer $ACCESS" -H "x-goog-user-project: ${PROJECT_ID}" -H "Content-Type: application/json")

# Look the agent up across all pages. A failed list call must not read as
# "not found" (that would register a duplicate), and an agent with the same
# display name but another card URL belongs to someone else: never take it over.
find_agent() {
  local page="" out="$WORK/list.json" status
  while :; do
    status="$("${CURL[@]}" -o "$out" -w '%{http_code}' "${H[@]}" \
      "$BASE?pageSize=100${page:+&pageToken=$page}")"
    if [ "${status:0:1}" != "2" ]; then
      echo "Listing Gemini Enterprise agents failed (HTTP $status):" >&2
      cat "$out" >&2
      return 2
    fi
    python3 - "$out" "$DISPLAY_NAME" "$AGENT_URL" > "$WORK/match.txt" <<'PY' || return $?
import json, sys
path, name, url = sys.argv[1:]
data = json.load(open(path))
for a in data.get("agents", []):
    if a.get("displayName") != name:
        continue
    try:
        card_url = json.loads(a["a2aAgentDefinition"]["jsonAgentCard"]).get("url", "")
    except (KeyError, TypeError, ValueError):
        card_url = ""
    if card_url.rstrip("/") == url.rstrip("/"):
        print("MATCH", a["name"])
        sys.exit(0)
    print(f"Agent '{name}' already exists ({a['name']}) but points to {card_url or 'an unknown URL'}, "
          f"not {url}. Set a different agent_display_name.", file=sys.stderr)
    sys.exit(3)
print("NEXT", data.get("nextPageToken", ""))
PY
    read -r kind value < "$WORK/match.txt" || true
    if [ "$kind" = "MATCH" ]; then echo "$value"; return 0; fi
    page="${value:-}"
    [ -n "$page" ] || return 0
  done
}
EXISTING="$(find_agent)"

if [ -n "$EXISTING" ]; then
  echo "Updating $EXISTING ..."
  URL="https://discoveryengine.googleapis.com/v1alpha/${EXISTING}?updateMask=displayName,description,a2aAgentDefinition,sharingConfig"
  METHOD=PATCH
else
  echo "Creating agent in engine $ENGINE_ID ..."
  URL="$BASE"
  METHOD=POST
fi

# Licence assignment may still be propagating (FAILED_PRECONDITION); retry
# that and transient errors, fail fast on anything else.
for attempt in 1 2 3 4 5; do
  STATUS="$("${CURL[@]}" -o "$WORK/resp.json" -w '%{http_code}' -X "$METHOD" "${H[@]}" -d @"$BODY" "$URL")"
  [ "${STATUS:0:1}" = "2" ] && break
  if [ "$STATUS" = "429" ] || [ "${STATUS:0:1}" = "5" ] || grep -q FAILED_PRECONDITION "$WORK/resp.json"; then
    echo "Registration returned HTTP $STATUS (attempt $attempt); retrying in 20s ..."
    sleep 20
    continue
  fi
  break
done
if [ "${STATUS:0:1}" != "2" ]; then
  echo "Gemini Enterprise registration failed (HTTP $STATUS):" >&2
  cat "$WORK/resp.json" >&2
  exit 1
fi
echo "Registered: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("name",""))' "$WORK/resp.json")"
