"""Phase 0 spike probe: exercise the local A2UI reference agent over A2A.

Sends a text message with the A2UI extension activated and prints the A2UI
messages the agent returns, so we can confirm the wire shape end to end.
"""

import json
import sys
import urllib.error
import urllib.request

import os

# Override with A2UI_ENDPOINT to target the reference agent (:10002),
# the echo probe (:8080), or a deployed Cloud Run URL.
ENDPOINT = os.environ.get("A2UI_ENDPOINT", "http://localhost:8080")
EXTENSION = "https://a2ui.org/a2a-extension/a2ui/v0.9"

prompt = sys.argv[1] if len(sys.argv) > 1 else "What can you do?"

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
            "messageId": "spike-1",
            "kind": "message",
        }
    },
}

req = urllib.request.Request(
    ENDPOINT,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "X-A2A-Extensions": EXTENSION},
)

try:
    body = urllib.request.urlopen(req, timeout=180).read()
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code)
    print(exc.read().decode()[:2000])
    raise SystemExit(1)

resp = json.loads(body)

if "error" in resp:
    print("RPC ERROR:", json.dumps(resp["error"])[:1500])
    raise SystemExit(1)

status = resp.get("result", {}).get("status", {})
print("task state:", status.get("state"))

parts = status.get("message", {}).get("parts", [])
print(f"parts returned: {len(parts)}\n")

for i, part in enumerate(parts):
    kind = part.get("kind")
    print(f"--- part {i}  kind={kind}")
    if kind == "data":
        data = part.get("data", {})
        # An A2UI message is identified by its single payload key.
        keys = [k for k in data if k != "version"]
        print("   version:", data.get("version"), "| message type:", keys)
        print("   ", json.dumps(data)[:700])
    else:
        print("   ", str(part.get("text"))[:500])
    print()
