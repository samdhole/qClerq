"""Bypass Compute File Hash node in Drive path.
Backend computes file_hash server-side — the n8n crypto node is redundant and
fails in newer n8n sandbox (crypto module disallowed).
New path: Download Invoice PDF -> Call /extract API
"""
import json, urllib.request, pathlib

src = pathlib.Path(__file__).parent / "patch_workflow_api.py"
API_KEY = src.read_text().split('API_KEY = "')[1].split('"')[0]
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
conns = wf["connections"]

# Change: Download Invoice PDF -> Compute File Hash -> Call /extract API
# To:     Download Invoice PDF -> Call /extract API
if "Download Invoice PDF" in conns:
    for branch in conns["Download Invoice PDF"].get("main", []):
        for edge in branch:
            if edge.get("node") == "Compute File Hash":
                edge["node"] = "Call /extract API"
                print(f"Fixed: Download Invoice PDF -> Call /extract API (bypassed Compute File Hash)")

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
print(f"Updated: {result['id']}")
api("POST", f"/workflows/{WF_ID}/activate")
print("Activated")

wf2 = api("GET", f"/workflows/{WF_ID}")
dest = wf2["connections"].get("Download Invoice PDF", {}).get("main", [[]])[0]
print(f"Download Invoice PDF now routes to: {[e['node'] for e in dest]}")
