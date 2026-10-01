#!/bin/bash
# Makes sure the demo users hold a Gemini Enterprise licence.
#
# A fresh Argolis project has no GE licence, and the agent registration API
# refuses with FAILED_PRECONDITION ("an active Gemini Enterprise license is
# not available") until the caller has one. This script:
#   1. starts the one-month GE free trial if the project has no active
#      licence config (licenseConfigs.create is allowed for free trials), and
#   2. assigns a licence to each user in LICENSE_USERS.
#
# Idempotent: an existing active licence config is reused, and assigning an
# already-assigned user is a no-op.
#
# Required env: PROJECT_ID, PROJECT_NUMBER, LICENSE_USERS (space-separated).
# Optional env: ACCESS_TOKEN (OAuth token for the Discovery Engine API).
# Called by click-to-deploy/demo/terraform/gemini_enterprise.tf.

set -euo pipefail

: "${PROJECT_ID:?}" "${PROJECT_NUMBER:?}" "${LICENSE_USERS:?}"

API="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global"
ACCESS="${ACCESS_TOKEN:-$(gcloud auth print-access-token)}"
H=(-H "Authorization: Bearer $ACCESS" -H "x-goog-user-project: ${PROJECT_ID}" -H "Content-Type: application/json")

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- 1. Find or create an active licence config -----------------------------
curl -sf "${H[@]}" "$API/licenseConfigs" -o "$WORK/configs.json"
CONFIG="$(python3 - "$WORK/configs.json" <<'PY'
import json, sys
for c in json.load(open(sys.argv[1])).get("licenseConfigs", []):
    if c.get("state") == "ACTIVE":
        print(c["name"]); break
PY
)"

if [ -z "$CONFIG" ]; then
  echo "No active Gemini Enterprise licence in ${PROJECT_ID}; starting the free trial ..."
  STATUS="$(curl -s -o "$WORK/create.json" -w '%{http_code}' -X POST "${H[@]}" \
    "$API/licenseConfigs?licenseConfigId=free_trial_gemini" \
    -d '{"licenseCount":"50","subscriptionTier":"SUBSCRIPTION_TIER_SEARCH_AND_ASSISTANT","subscriptionTerm":"SUBSCRIPTION_TERM_ONE_MONTH","freeTrial":true,"geminiBundle":true}')"
  if [ "${STATUS:0:1}" != "2" ]; then
    echo "Could not start the Gemini Enterprise free trial (HTTP $STATUS):" >&2
    cat "$WORK/create.json" >&2
    echo "Start a trial or assign a licence in the console, then re-apply." >&2
    exit 1
  fi
  CONFIG="projects/${PROJECT_NUMBER}/locations/global/licenseConfigs/free_trial_gemini"
fi
echo "Using licence config $CONFIG"

# --- 2. Assign a licence to each demo user ----------------------------------
python3 - "$CONFIG" $LICENSE_USERS > "$WORK/assign.json" <<'PY'
import json, sys
config, users = sys.argv[1], sys.argv[2:]
print(json.dumps({"inlineSource": {"userLicenses": [
    {"userPrincipal": u, "licenseConfig": config} for u in users
]}}))
PY

STATUS="$(curl -s -o "$WORK/assigned.json" -w '%{http_code}' -X POST "${H[@]}" \
  "$API/userStores/default_user_store:batchUpdateUserLicenses" -d @"$WORK/assign.json")"
if [ "${STATUS:0:1}" != "2" ]; then
  echo "Licence assignment failed (HTTP $STATUS):" >&2
  cat "$WORK/assigned.json" >&2
  exit 1
fi

python3 - "$WORK/assigned.json" <<'PY'
import json, sys
op = json.load(open(sys.argv[1]))
for lic in op.get("response", {}).get("userLicenses", []):
    print(f"  {lic.get('userPrincipal')}: {lic.get('licenseAssignmentState')}")
if op.get("error"):
    sys.exit(f"Licence assignment error: {op['error']}")
PY
