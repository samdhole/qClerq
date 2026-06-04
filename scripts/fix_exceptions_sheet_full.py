"""Full fix for Write to Exceptions Sheet node:
- Align schema with actual EXCEPTION_COLUMNS from sheets_sync.py
- Fix value mapping expressions to use correct field paths
- Actual columns: file_hash, file_name, vendor_normalized, invoice_number,
  issue_type, severity, message, status, created_at
"""
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

# Schema matching EXCEPTION_COLUMNS in sheets_sync.py
SCHEMA = [
    {"id": "file_hash",        "displayName": "file_hash",        "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "file_name",        "displayName": "file_name",        "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "vendor_normalized","displayName": "vendor_normalized","required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "invoice_number",   "displayName": "invoice_number",   "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "issue_type",       "displayName": "issue_type",       "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "severity",         "displayName": "severity",         "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "message",          "displayName": "message",          "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "status",           "displayName": "status",           "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
    {"id": "created_at",       "displayName": "created_at",       "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "type": "string"},
]

# Value mapping — pull invoice fields from extract API, exception from validate
VALUE = {
    "file_hash":         "={{ $('Call /extract API').item.json.file_hash }}",
    "file_name":         "={{ $('Call /extract API').item.json.file_name }}",
    "vendor_normalized": "={{ $('Call /extract API').item.json.vendor_normalized }}",
    "invoice_number":    "={{ $('Call /extract API').item.json.invoice_number }}",
    "issue_type":        "={{ ($json.exceptions && $json.exceptions[0]) ? $json.exceptions[0].type : ($('Call /extract API').item.json.duplicate_risk !== 'none' ? 'duplicate_risk' : 'low_confidence') }}",
    "severity":          "={{ ($json.exceptions && $json.exceptions[0]) ? $json.exceptions[0].severity : 'high' }}",
    "message":           "={{ ($json.exceptions || []).map(e => e.message).join('; ') || $('Call /extract API').item.json.warnings.join('; ') || 'Exception detected' }}",
    "status":            "open",
    "created_at":        "={{ new Date().toISOString() }}",
}

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
        node["parameters"]["columns"] = {
            "mappingMode": "defineBelow",
            "value": VALUE,
            "schema": SCHEMA,
        }
        print(f"Updated Write to Exceptions Sheet: {len(SCHEMA)} columns, {len(VALUE)} mapped values")
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
print("Activated — drop another file to verify Exceptions sheet write")
