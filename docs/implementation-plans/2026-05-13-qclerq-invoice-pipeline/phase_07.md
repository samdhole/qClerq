# qClerq Invoice Pipeline — Phase 7: n8n Workflow Update + Proof Trail

**Goal:** Replace n8n Gemini extraction with Python backend calls and wire the full approval + proof trail flow.

**Architecture:** Remove 3 LangChain nodes (`Invoice Parser AI Agent`, `Gemini Chat Model`, `Structured Output Parser`). Replace with: HTTP Request → `POST /extract` (multipart), HTTP Request → `POST /validate` (JSON), Switch node → 3-way split on `approval_tier`. Wire auto-approve path directly to `POST /sync`. Wire manager/CFO paths to existing approval email node. Fill 3 placeholder values. Update `workflows/CONTEXT.md`.

**Tech Stack:** n8n (self-hosted), n8n-nodes-base.httpRequest (typeVersion 4.2), n8n-nodes-base.switch (typeVersion 3), JSON editing

**Scope:** Phase 7 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC1: Invoice intake via all 3 paths
- **qclerq-invoice-pipeline.AC1.1 Success:** Gmail attachment PDF triggers workflow and reaches /extract
- **qclerq-invoice-pipeline.AC1.2 Success:** Drive folder new file triggers workflow and reaches /extract
- **qclerq-invoice-pipeline.AC1.3 Success:** Web upload form submission reaches /extract
- **qclerq-invoice-pipeline.AC1.4 Failure:** Non-PDF file attachment is ignored / skipped

### qclerq-invoice-pipeline.AC3: 3-tier approval routing
- **qclerq-invoice-pipeline.AC3.1 Success:** Invoice total < $500 → approval_tier = "auto", no approval email sent
- **qclerq-invoice-pipeline.AC3.2 Success:** Invoice total $500–$5000 → approval_tier = "manager", approval email sent
- **qclerq-invoice-pipeline.AC3.3 Success:** Invoice total > $5000 → approval_tier = "cfo", approval email sent
- **qclerq-invoice-pipeline.AC3.4 Success:** Approval callback records approved_by, approval_notes, approved_at in Sheets row

---

## Discrepancy Notes

- Actual workflow has **17 nodes**, not 15 as design assumed (3rd intake path: `Web Upload Form` + `Extract Text from Form PDF` already present).
- `Route Exceptions` (IF node) currently branches TRUE for exceptions — this is correct; FALSE path (clean invoices) currently has no connection.
- The current `Check Approval Decision` node is a binary approval/reject IF — the 3-way tier split (auto/manager/cfo) does NOT exist yet.
- `Send Invoice for Approval` uses `sendAndWait` pattern — this is the correct n8n Gmail approval pattern; retain it.
- Node ID for `Invoice Parser AI Agent` (to remove): `fc5a0c82-a1e9-468d-a2a5-0f8e7291f1d9`
- Node ID for `Gemini Chat Model` (to remove): `59b4e37b-f52f-415a-bb6f-7a249984ec3a`
- Node ID for `Structured Output Parser` (to remove): `d9d53795-da16-4921-9c50-a4ce036b564f`
- For multipart upload: the `Extract Text from Drive/Email/Form PDF` nodes output binary data on property `"data"` — reference as `inputDataFieldName: "data"`.
- The `POST /extract` endpoint takes the raw PDF (the HTTP handler computes the hash) — do NOT send extracted text to `/extract`. Instead, send the raw PDF binary to `/extract` as `multipart/form-data`.

---

## Workflow Edit Strategy

Edit `workflows/n8n_invoice_desk.json` directly as JSON. After editing, import via n8n UI → Settings → Import → paste/upload JSON. Re-attach all Google OAuth, Gmail, and Sheets credentials after import.

---

<!-- START_SUBCOMPONENT_A (tasks 1-4) -->

<!-- START_TASK_1 -->
### Task 1: Remove the three LangChain nodes from n8n_invoice_desk.json

**Verifies:** None (cleanup)

**Files:**
- Modify: `workflows/n8n_invoice_desk.json`

**Step 1: Open `workflows/n8n_invoice_desk.json` in a text editor**

**Step 2: Remove the following three node objects from the `nodes` array**

Delete entire node objects with these IDs:
- `"id": "fc5a0c82-a1e9-468d-a2a5-0f8e7291f1d9"` (Invoice Parser AI Agent)
- `"id": "59b4e37b-f52f-415a-bb6f-7a249984ec3a"` (Gemini Chat Model)
- `"id": "d9d53795-da16-4921-9c50-a4ce036b564f"` (Structured Output Parser)

**Step 3: Remove all connection entries referencing these node IDs from the `connections` object**

Delete any connection entries where the source or destination is one of the three removed IDs. The LangChain sub-node connections use `"ai_languageModel"` and `"ai_outputParser"` connection type keys — remove those entire blocks.

The three extract nodes (`Extract Text from Drive PDF`, `Extract Text from Email PDF`, `Extract Text from Form PDF`) currently connect to `Invoice Parser AI Agent` as their `main[0][0]` output destination. Remove those destination entries — they will be reconnected in Task 2.

**Step 4: Verify the JSON is still valid**

```bash
python -c "import json; json.loads(open('workflows/n8n_invoice_desk.json').read()); print('valid JSON')"
```

Expected: prints `valid JSON`

**Step 5: Commit**

```bash
git add workflows/n8n_invoice_desk.json
git commit -m "feat(n8n): remove Gemini/LangChain extraction nodes"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Add HTTP Request → POST /extract node

**Verifies:** qclerq-invoice-pipeline.AC1.1, qclerq-invoice-pipeline.AC1.2, qclerq-invoice-pipeline.AC1.3

**Files:**
- Modify: `workflows/n8n_invoice_desk.json`

**Step 1: Add the HTTP Request node for POST /extract to the `nodes` array**

Add this node object (choose a position on the canvas to the right of the three Extract Text nodes):

```json
{
  "id": "call-extract-api",
  "name": "Call /extract API",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [900, 300],
  "parameters": {
    "method": "POST",
    "url": "http://localhost:8000/extract",
    "sendBody": true,
    "contentType": "multipart-form-data",
    "bodyParameters": {
      "parameters": [
        {
          "name": "file",
          "parameterType": "formBinaryData",
          "inputDataFieldName": "data"
        }
      ]
    },
    "options": {}
  }
}
```

**Step 2: Wire the three Extract Text nodes to this new node**

In the `connections` object, add `"call-extract-api"` as a `main[0]` destination for each of the three extract nodes:
- `Extract Text from Drive PDF` → `Call /extract API`
- `Extract Text from Email PDF` → `Call /extract API`
- `Extract Text from Form PDF` → `Call /extract API`

Connection format:
```json
"Extract Text from Drive PDF": {
  "main": [[{"node": "Call /extract API", "type": "main", "index": 0}]]
}
```

**Step 3: Verify JSON valid**

```bash
python -c "import json; json.loads(open('workflows/n8n_invoice_desk.json').read()); print('valid JSON')"
```

**Step 4: Commit**

```bash
git add workflows/n8n_invoice_desk.json
git commit -m "feat(n8n): add HTTP Request node for POST /extract"
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Add HTTP Request → POST /validate node and Switch node for 3-way tier split

**Verifies:** qclerq-invoice-pipeline.AC3.1, qclerq-invoice-pipeline.AC3.2, qclerq-invoice-pipeline.AC3.3

**Files:**
- Modify: `workflows/n8n_invoice_desk.json`

**Step 1: Add HTTP Request → POST /validate node**

```json
{
  "id": "call-validate-api",
  "name": "Call /validate API",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [1100, 300],
  "parameters": {
    "method": "POST",
    "url": "http://localhost:8000/validate",
    "sendBody": true,
    "contentType": "json",
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify($json) }}",
    "options": {}
  }
}
```

Wire: `Call /extract API` → `Call /validate API` (main[0])

**Step 2: Add Switch node for approval_tier 3-way split**

```json
{
  "id": "approval-tier-switch",
  "name": "Route by Approval Tier",
  "type": "n8n-nodes-base.switch",
  "typeVersion": 3,
  "position": [1300, 300],
  "parameters": {
    "mode": "rules",
    "rules": {
      "values": [
        {
          "conditions": {
            "options": {"caseSensitive": true, "leftValue": "", "typeValidation": "strict"},
            "conditions": [{"leftValue": "={{ $json.approval_tier }}", "rightValue": "auto", "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "auto"
        },
        {
          "conditions": {
            "options": {"caseSensitive": true, "leftValue": "", "typeValidation": "strict"},
            "conditions": [{"leftValue": "={{ $json.approval_tier }}", "rightValue": "manager", "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "manager"
        },
        {
          "conditions": {
            "options": {"caseSensitive": true, "leftValue": "", "typeValidation": "strict"},
            "conditions": [{"leftValue": "={{ $json.approval_tier }}", "rightValue": "cfo", "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "cfo"
        }
      ]
    },
    "fallbackOutput": "none"
  }
}
```

Wire: `Call /validate API` → `Route by Approval Tier` (main[0])

**Step 3: Wire outputs**
- Output 0 (auto): → `Call /sync API` (HTTP Request node added in Task 4)
- Output 1 (manager): → existing `Send Invoice for Approval` node
- Output 2 (cfo): → existing `Send Invoice for Approval` node (same node, both manager + CFO require approval email)

**Step 4: Verify JSON valid and commit**

```bash
python -c "import json; json.loads(open('workflows/n8n_invoice_desk.json').read()); print('valid JSON')"
git add workflows/n8n_invoice_desk.json
git commit -m "feat(n8n): add /validate HTTP Request and 3-way approval tier Switch node"
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Add POST /sync HTTP Request node and fill placeholders

**Verifies:** qclerq-invoice-pipeline.AC3.4, qclerq-invoice-pipeline.AC1.4

**Files:**
- Modify: `workflows/n8n_invoice_desk.json`

**Step 1: Add HTTP Request → POST /sync node (for auto-approve path)**

```json
{
  "id": "call-sync-api",
  "name": "Call /sync API",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [1500, 200],
  "parameters": {
    "method": "POST",
    "url": "http://localhost:8000/sync",
    "sendBody": true,
    "contentType": "json",
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ invoice: $('Call /extract API').item.json, approved_by: 'auto-approved', approval_notes: '', approved_at: new Date().toISOString(), approval_tier: 'auto' }) }}",
    "options": {}
  }
}
```

Wire: `Route by Approval Tier` output 0 (auto) → `Call /sync API`

**Step 1b: Add HTTP Request → POST /approval-callback node (for manager/CFO paths)**

The `Send Invoice for Approval` node uses `sendAndWait` — n8n resumes on approval and makes the resume request available at `$json`. After `Check Approval Decision` (the existing approval/reject IF node), the approval path must call `POST /approval-callback` with the approval payload.

Add this node:

```json
{
  "id": "call-approval-callback",
  "name": "Call /approval-callback",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [1500, 400],
  "parameters": {
    "method": "POST",
    "url": "http://localhost:8000/approval-callback",
    "sendBody": true,
    "contentType": "json",
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ invoice: $('Call /extract API').item.json, approved_by: $json.approvedBy || $json.approved_by || 'approver', approval_notes: $json.approvalNotes || $json.approval_notes || '', approved_at: new Date().toISOString(), approval_tier: $('Call /validate API').item.json.approval_tier }) }}",
    "options": {}
  }
}
```

Wire: `Check Approval Decision` TRUE output → `Call /approval-callback`

This satisfies AC3.4: the approval callback records `approved_by`, `approval_notes`, `approved_at` and triggers sync.

**Step 2: Fill the 3 placeholder values**

In `workflows/n8n_invoice_desk.json`, find and replace the literal placeholder strings:

| Placeholder | JSON key location | Replace with |
|---|---|---|
| `"REPLACE_WITH_DRIVE_FOLDER_ID"` | `Invoice Folder Monitor` node → `parameters.folderToWatch.value` | The actual Google Drive folder ID for invoice drop (get from Drive URL) |
| `"REPLACE_WITH_APPROVER_EMAIL"` | `Send Invoice for Approval` node → `parameters.sendTo` | `enigman.kk@gmail.com` (or real approver) |
| `"REPLACE_WITH_FINANCE_EMAIL"` | `Send Rejection Notification` node → `parameters.sendTo` | `enigman.kk@gmail.com` (or real finance contact) |

**Step 3: Add PDF filters to all three intake paths (AC1.4)**

Non-PDF files must be silently ignored at the trigger level, not errored downstream.

**Gmail path** — In the `Monitor Gmail Invoices` node (ID: `2224840e-b38b-4375-9726-4355db099d6c`), set `parameters.filters.query` to: `has:attachment (invoice OR receipt OR bill) filename:pdf`. If not already set, add this filter string.

**Drive path** — In the `Invoice Folder Monitor` node, add a mimeType filter or add an IF node immediately after it:

```json
{
  "id": "filter-pdf-drive",
  "name": "Is PDF? (Drive)",
  "type": "n8n-nodes-base.if",
  "typeVersion": 2,
  "position": [400, 100],
  "parameters": {
    "conditions": {
      "options": {"caseSensitive": false},
      "conditions": [
        {
          "leftValue": "={{ $binary.data.mimeType }}",
          "rightValue": "application/pdf",
          "operator": {"type": "string", "operation": "equals"}
        }
      ]
    }
  }
}
```

Wire: `Invoice Folder Monitor` → `Is PDF? (Drive)` → TRUE → `Download Invoice PDF`. FALSE output has no connection (silently dropped).

**Web Form path** — In the `Web Upload Form` node, if it supports `acceptFileTypes`, set it to `application/pdf`. Additionally add a mimeType IF guard identical to the Drive path between `Extract Text from Form PDF` and `Call /extract API` — or more precisely, add an IF check after the form trigger before extraction.

**Python /extract defense in depth** — In phase_05.md `api.py`, add MIME guard at the top of the `/extract` handler:

```python
if file.content_type not in ("application/pdf", "application/octet-stream"):
    raise HTTPException(status_code=415, detail=f"Only PDF files accepted, got: {file.content_type}")
```

This satisfies AC1.4 at every layer: Gmail (query filter), Drive (IF node), Form (field restriction + IF guard), Python (415).

**Step 4: Verify JSON valid and commit**

```bash
python -c "import json; json.loads(open('workflows/n8n_invoice_desk.json').read()); print('valid JSON')"
git add workflows/n8n_invoice_desk.json
git commit -m "feat(n8n): add /sync HTTP Request node, fill placeholder values, PDF filters on all intake paths (AC1.4)"
```
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 5-6) -->

<!-- START_TASK_5 -->
### Task 5: Import updated workflow into n8n and verify end-to-end

**Verifies:** qclerq-invoice-pipeline.AC1.1, qclerq-invoice-pipeline.AC1.3, qclerq-invoice-pipeline.AC3.1

**Files:** None (operational verification)

**Step 1: Start the FastAPI backend**

```bash
uvicorn src.app.main:app --reload --host 127.0.0.1 --port 8000
```

**Step 2: Import workflow into n8n**

1. Open n8n UI
2. Go to Settings → Import → Workflow
3. Paste / upload `workflows/n8n_invoice_desk.json`
4. After import: **re-attach all credentials** (Google OAuth2, Gmail OAuth2, Google Sheets OAuth2). Credentials do not survive export/import between n8n instances.

**Step 3: Test via web upload form (AC1.3)**

1. Open the `Web Upload Form` node → note the form URL
2. Open the form URL in a browser
3. Upload a test PDF (any PDF file)
4. Watch n8n execute step-by-step in the execution log
5. Verify: n8n calls `POST /extract` and gets `InvoiceExtracted` back
6. Verify: n8n calls `POST /validate` and gets `ValidationResult` with `approval_tier` set

**Step 4: Verify auto-approve path**

Submit a test PDF representing a low-value invoice (< $500):
- Verify `approval_tier = "auto"` in `/validate` response
- Verify Switch node routes to output 0 (auto)
- Verify `POST /sync` is called

**Step 5: Test Gmail path (AC1.1)**

Send an email to the monitored Gmail address with a PDF attachment. Verify the workflow triggers and reaches `/extract`.

**Step 6: Note any errors or unexpected behavior**

If `/extract` returns 422 (extraction failed), check:
- FastAPI logs for the error message
- LlamaParse API key is valid in `.env`
- PDF is a readable digital PDF (not a scan)
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Update workflows/CONTEXT.md

**Verifies:** None (documentation)

**Files:**
- Modify: `workflows/CONTEXT.md`

**Step 1: Update workflows/CONTEXT.md**

Add or update the following sections:

1. **Node inventory** — replace the old node list with the new 19-node structure (17 original − 3 removed LangChain nodes + 5 new nodes: Call /extract API, Call /validate API, Route by Approval Tier, Call /sync API, Call /approval-callback, Is PDF? Drive filter)

2. **Credential re-attachment instructions** — add explicit step-by-step for after import:
   - Google OAuth2: attach to `Invoice Folder Monitor` and `Download Invoice PDF`
   - Gmail OAuth2: attach to `Monitor Gmail Invoices`, `Send Invoice for Approval`, `Send Rejection Notification`
   - Google Sheets OAuth2: attach to `Write to Invoices Sheet`, `Write to Exceptions Sheet`

3. **Backend URL** — document that `http://localhost:8000` is used for HTTP Request nodes in dev; change to production URL when deploying.

4. **Approval flow** — document the new 3-way tier split: auto → /sync directly; manager/CFO → approval email → sendAndWait → /sync.

**Step 2: Commit**

```bash
git add workflows/CONTEXT.md
git commit -m "docs(n8n): update CONTEXT.md with new node structure and credential re-attachment steps"
```
<!-- END_TASK_6 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 7 Done When

- `workflows/n8n_invoice_desk.json` validates as JSON and imports into n8n without errors
- Web upload form path: PDF → n8n → `/extract` → `/validate` → (auto) → `/sync` completes
- Gmail attachment path reaches `/extract`
- Drive folder watch path reaches `/extract`
- For total < $500: no approval email sent (auto-approve path taken)
- For total ≥ $500: approval email sent to configured approver address
