#!/usr/bin/env bash
# Confirms the freshly deployed probe is serving before a manual GE test.
# A 403 here means the identity token is missing; a 404 means the route moved.
set -uo pipefail

URL="https://a2ui-echo-probe-g22bhpwccq-uc.a.run.app"
TOKEN="$(gcloud auth print-identity-token)"

CODE=$(curl -s -o /tmp/card.json -w '%{http_code}' \
  -H "Authorization: Bearer ${TOKEN}" \
  "${URL}/.well-known/agent-card.json")

echo "card_http=${CODE}"
python3 - <<'PY'
import json
try:
    d = json.load(open("/tmp/card.json"))
except Exception as exc:
    print("card unreadable:", exc)
    raise SystemExit(0)
print("name:", d.get("name"))
print("url :", d.get("url"))
exts = d.get("capabilities", {}).get("extensions", [])
for e in exts:
    print("ext :", e.get("uri"))
    params = e.get("params") or {}
    for cid in params.get("supportedCatalogIds", []):
        print("  catalog:", cid)
PY
