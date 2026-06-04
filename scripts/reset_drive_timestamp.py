"""Reset Drive trigger lastTimeChecked so it re-scans recent files."""
import json, os, urllib.request, datetime

API_KEY = os.environ.get("N8N_API_KEY")
if not API_KEY:
    raise SystemExit(
        "ERROR: environment variable N8N_API_KEY is not set. "
        "Export it before running this script (see docs/SECRETS_ROTATION.md)."
    )
BASE = "http://localhost:5678/api/v1"
WF_ID = "QCG7orEdmyfZlUpx"
HEADERS = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}

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
static = wf.get("staticData", {}) or {}

# Reset to 20 minutes ago
new_ts = (datetime.datetime.utcnow() - datetime.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
if "node:Invoice Folder Monitor" not in static:
    static["node:Invoice Folder Monitor"] = {}
static["node:Invoice Folder Monitor"]["lastTimeChecked"] = new_ts
print(f"Resetting Drive lastTimeChecked to: {new_ts}")

settings = {k: v for k, v in wf.get("settings", {}).items() if k != "binaryMode"}
body = {
    "name": wf["name"],
    "nodes": wf["nodes"],
    "connections": wf["connections"],
    "settings": settings,
    "staticData": static,
}

api("POST", f"/workflows/{WF_ID}/deactivate")
print("Deactivated")
result = api("PUT", f"/workflows/{WF_ID}", body)
print(f"Updated: {result['id']}")
api("POST", f"/workflows/{WF_ID}/activate")
print("Activated — next poll will pick up files from the last 20 min")
