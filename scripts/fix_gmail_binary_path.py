"""Fix Gmail trigger path: replace 'Extract Text from Email PDF' (extractFromFile)
with a Code node that renames attachment_0 → data so the binary passes through
to Call /extract API correctly.

The LlamaParse-era extractFromFile node strips the binary. Since we now send
raw PDF bytes directly to Gemini, we need the binary preserved.
"""
import json
import os
import urllib.request
import urllib.error

API_KEY = os.environ.get("N8N_API_KEY")
if not API_KEY:
    raise SystemExit(
        "ERROR: environment variable N8N_API_KEY is not set. "
        "Export it before running this script (see docs/SECRETS_ROTATION.md)."
    )

BASE = "http://localhost:5678/api/v1"
WF_ID = "QCG7orEdmyfZlUpx"
GMAIL_RENAME_NODE_ID = "51b5bedc-ac31-4e31-a854-89d63c8fe410"
HEADERS = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}


def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}")
        raise


# GET current workflow
print("Fetching workflow...")
wf = api("GET", f"/workflows/{WF_ID}")
print(f"Got: {wf['name']} | active={wf['active']}")

# Find and replace the Extract Text from Email PDF node
nodes = wf["nodes"]
fixed = False
for i, node in enumerate(nodes):
    if node["id"] == GMAIL_RENAME_NODE_ID:
        print(f"Found node: '{node['name']}' (type: {node['type']})")
        # Replace with a Code node that renames attachment_0 → data
        nodes[i] = {
            "id": GMAIL_RENAME_NODE_ID,
            "name": "Rename Gmail Binary",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": node["position"],
            "parameters": {
                "jsCode": (
                    "// Rename Gmail attachment binary field from 'attachment_0' to 'data'\n"
                    "// so Call /extract API can find it via inputDataFieldName: data\n"
                    "const binary = $input.first().binary;\n"
                    "const key = Object.keys(binary).find(k => k.startsWith('attachment')) "
                    "|| Object.keys(binary)[0];\n"
                    "if (!key) throw new Error('No binary attachment found in Gmail item');\n"
                    "const newBinary = { data: binary[key] };\n"
                    "return [{ json: $input.first().json, binary: newBinary }];"
                )
            }
        }
        fixed = True
        print(f"  -> Replaced with Code node 'Rename Gmail Binary'")
        break

if not fixed:
    print("ERROR: Node not found — already fixed or ID changed")
    sys.exit(1)

# Build PUT body (exclude settings keys that n8n API rejects)
allowed_settings = {
    k: v for k, v in wf.get("settings", {}).items()
    if k not in ("binaryMode",)
}
body = {
    "name": wf["name"],
    "nodes": nodes,
    "connections": wf["connections"],
    "settings": allowed_settings,
    "staticData": wf.get("staticData"),
}

# Deactivate first so n8n doesn't try to validate OAuth during PUT
print("Deactivating workflow first...")
try:
    api("POST", f"/workflows/{WF_ID}/deactivate")
    print("Deactivated")
except Exception as e:
    print(f"Deactivate warning (may already be inactive): {e}")

print("Pushing updated workflow...")
result = api("PUT", f"/workflows/{WF_ID}", body)
print(f"Updated: id={result.get('id')} | active={result.get('active')}")

print("Re-activating...")
try:
    activate = api("POST", f"/workflows/{WF_ID}/activate")
    print(f"Activated: {activate.get('active')}")
except Exception as e:
    print(f"Re-activation failed (OAuth token may need refresh in browser): {e}")
    print("Fix IS applied — just re-activate manually in n8n UI after refreshing OAuth")

print("\nDone! Gmail path now: Monitor Gmail Invoices → Rename Gmail Binary → Call /extract API")
