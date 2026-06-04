"""Fix Write to Exceptions Sheet: add required schema array for Google Sheets node v4.5."""
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

SCHEMA = [
    {"id": "invoice_id",  "displayName": "invoice_id",  "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "issue_type",  "displayName": "issue_type",  "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "severity",    "displayName": "severity",    "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "message",     "displayName": "message",     "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "status",      "displayName": "status",      "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
]

def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:300]}")
        raise

wf = api("GET", f"/workflows/{WF_ID}")

for node in wf["nodes"]:
    if node["name"] == "Write to Exceptions Sheet":
        cols = node["parameters"].get("columns", {})
        cols["mappingMode"] = "defineBelow"
        cols["schema"] = SCHEMA
        node["parameters"]["columns"] = cols
        print(f"Added schema with {len(SCHEMA)} columns to Write to Exceptions Sheet")
        break

settings = {k: v for k, v in wf.get("settings", {}).items() if k != "binaryMode"}
body = {
    "name": wf["name"],
    "nodes": wf["nodes"],
    "connections": wf["connections"],
    "settings": settings,
    "staticData": wf.get("staticData"),
}

api("POST", f"/workflows/{WF_ID}/deactivate")
print("Deactivated")
result = api("PUT", f"/workflows/{WF_ID}", body)
print(f"Updated: {result['id']}")
api("POST", f"/workflows/{WF_ID}/activate")
print("Activated — schema added, drop another file to test")
