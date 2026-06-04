"""Fix Gmail connection name: update references from old node name to new."""
import json, os, urllib.request

API_KEY = os.environ.get("N8N_API_KEY")
if not API_KEY:
    raise SystemExit(
        "ERROR: environment variable N8N_API_KEY is not set. "
        "Export it before running this script (see docs/SECRETS_ROTATION.md)."
    )
BASE = "http://localhost:5678/api/v1"
WF_ID = "QCG7orEdmyfZlUpx"
HEADERS = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}

OLD = "Extract Text from Email PDF"
NEW = "Rename Gmail Binary"

def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:200]}")
        raise

wf = api("GET", f"/workflows/{WF_ID}")
conns = wf["connections"]

# Fix destination reference in Monitor Gmail Invoices
if "Monitor Gmail Invoices" in conns:
    for branch in conns["Monitor Gmail Invoices"].get("main", []):
        for edge in branch:
            if edge.get("node") == OLD:
                edge["node"] = NEW
                print(f"Fixed: Monitor Gmail Invoices -> {NEW}")

# Rename connection key
if OLD in conns:
    conns[NEW] = conns.pop(OLD)
    print(f"Renamed connection key: {OLD} -> {NEW}")

settings = {k: v for k, v in wf.get("settings", {}).items() if k != "binaryMode"}
body = {
    "name": wf["name"],
    "nodes": wf["nodes"],
    "connections": conns,
    "settings": settings,
    "staticData": wf.get("staticData"),
}

api("POST", f"/workflows/{WF_ID}/deactivate")
print("Deactivated")

result = api("PUT", f"/workflows/{WF_ID}", body)
print(f"PUT ok: {result['id']}")

api("POST", f"/workflows/{WF_ID}/activate")
print("Activated")

wf2 = api("GET", f"/workflows/{WF_ID}")
dest = wf2["connections"].get("Monitor Gmail Invoices", {}).get("main", [[]])[0]
print(f"Gmail now routes to: {[e['node'] for e in dest]}")
