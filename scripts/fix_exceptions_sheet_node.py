"""Fix Write to Exceptions Sheet node: add missing columns.mappingMode parameter."""
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
nodes = wf["nodes"]

for i, node in enumerate(nodes):
    if node["name"] == "Write to Exceptions Sheet":
        print(f"Found: {node['name']} (type: {node['type']})")
        cols = node["parameters"].get("columns", {})
        if "mappingMode" not in cols:
            cols["mappingMode"] = "defineBelow"
            node["parameters"]["columns"] = cols
            print(f"Added mappingMode=defineBelow to columns")
        else:
            print(f"mappingMode already set: {cols['mappingMode']}")
        break

settings = {k: v for k, v in wf.get("settings", {}).items() if k != "binaryMode"}
body = {
    "name": wf["name"],
    "nodes": nodes,
    "connections": wf["connections"],
    "settings": settings,
    "staticData": wf.get("staticData"),
}

api("POST", f"/workflows/{WF_ID}/deactivate")
print("Deactivated")
result = api("PUT", f"/workflows/{WF_ID}", body)
print(f"Updated: {result['id']}")
api("POST", f"/workflows/{WF_ID}/activate")
print("Activated - done!")
