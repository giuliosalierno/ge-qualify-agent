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
import base64, json, sys
card_path, display_name = sys.argv[1], sys.argv[2]

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0b57d0"/>
      <stop offset="55%" stop-color="#1a73e8"/>
      <stop offset="100%" stop-color="#4285f4"/>
    </linearGradient>
    <linearGradient id="spark" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="100%" stop-color="#a8c7fa"/>
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="64" fill="url(#bg)"/>
  <circle cx="64" cy="64" r="58" fill="none" stroke="rgba(255,255,255,0.22)" stroke-width="2"/>
  <rect x="28" y="30" width="58" height="70" rx="10" fill="#ffffff" opacity="0.96"/>
  <rect x="44" y="23" width="26" height="12" rx="6" fill="#d3e3fd" stroke="#0b57d0" stroke-width="2"/>
  <circle cx="40" cy="50" r="5" fill="#1a73e8"/>
  <rect x="50" y="47" width="26" height="6" rx="3" fill="#0b57d0"/>
  <circle cx="40" cy="66" r="5" fill="#fbbc04"/>
  <rect x="50" y="63" width="22" height="6" rx="3" fill="#444746"/>
  <circle cx="40" cy="82" r="5" fill="#34a853"/>
  <rect x="50" y="79" width="28" height="6" rx="3" fill="#137333"/>
  <path d="M95 24 C95 36, 99 40, 111 40 C99 40, 95 44, 95 56 C95 44, 91 40, 79 40 C91 40, 95 36, 95 24 Z" fill="url(#spark)"/>
</svg>"""
icon_data_uri = "data:image/svg+xml;base64," + base64.b64encode(ICON_SVG.encode("utf-8")).decode("ascii")

starter_prompts = [
    "Launch the agent",
]

card_obj = json.loads(open(card_path).read())
card_obj["name"] = display_name
exts = card_obj.setdefault("capabilities", {}).setdefault("extensions", [])
sp_uri = "https://www.googleapis.com/gemini-enterprise/a2a/extensions/starter_prompts/v1"
exts = [e for e in exts if e.get("uri") != sp_uri]
exts.append({
    "uri": sp_uri,
    "description": "Google Gemini Enterprise starter prompts extension to show contextually aware prompts on chat start.",
    "params": {"prompts": starter_prompts},
})
card_obj["capabilities"]["extensions"] = exts

print(json.dumps({
    "displayName": display_name,
    "description": (
        "Turn unstructured AI ideas into quantified Business Value Briefs, "
        "22-point Technical Architecture Dossiers, and a 4-quadrant CoE Portfolio.\n"
        "📋 Phase 1: Business Value Intake · 🏗️ Phase 2: Technical Architecture Review · 📊 Phase 3: CoE Portfolio Prioritization"
    ),
    "icon": {"content": icon_data_uri},
    "starterPrompts": [{"text": t} for t in starter_prompts],
    "customPlaceholderText": "Describe a new AI use case idea, or click Launch the agent above...",
    "a2aAgentDefinition": {"jsonAgentCard": json.dumps(card_obj)},
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
    try:
        card_url = json.loads(a["a2aAgentDefinition"]["jsonAgentCard"]).get("url", "")
    except (KeyError, TypeError, ValueError):
        card_url = ""
    if card_url.rstrip("/") == url.rstrip("/"):
        print("MATCH", a["name"])
        sys.exit(0)
    if a.get("displayName") == name:
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
  URL="https://discoveryengine.googleapis.com/v1alpha/${EXISTING}?updateMask=displayName,description,icon,starterPrompts,customPlaceholderText,a2aAgentDefinition,sharingConfig"
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
