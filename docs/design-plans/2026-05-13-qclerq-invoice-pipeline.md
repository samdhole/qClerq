# qClerq AI Invoice Pipeline Design

## Summary

qClerq AI Invoice Desk is an automated invoice intake system that accepts PDFs via three channels (Gmail attachments, Google Drive folder watch, and a web upload form), extracts structured data using Claude, validates it deterministically, routes it through a configurable approval tier, and syncs approved records to Google Sheets, QuickBooks, and Jobber — with a full proof trail on every row.

The approach deliberately separates concerns by runtime cost and reliability: ~60% of the work is deterministic code (SHA-256 deduplication, math checks, schema validation), ~30% is rule-based orchestration (approval routing thresholds, sync sequencing with partial-failure handling), and only ~10% is LLM calls (field extraction from raw PDF text and ambiguous vendor resolution). n8n handles all intake triggers and inter-service routing via HTTP; the Python FastAPI backend owns all intelligence and sync logic. No LLM calls live inside n8n nodes. The system is designed for per-client reuse by swapping a single `.env` file — no code changes between deployments.

## Definition of Done

A fully working demo where:

1. Invoices arrive via Gmail, Google Drive folder watch, or web upload form
2. Claude extracts structured JSON via Python FastAPI backend with confidence scoring, math validation (subtotal+tax±$0.02=total), and SHA-256 hash deduplication
3. Clean invoices route through 3-tier approval (auto-approve / manager / CFO by configurable amount thresholds), with approval gated before any sync runs
4. Approved records sync to Google Sheets + QuickBooks + Jobber with vendor normalization (raw → canonical name)
5. Exceptions (low confidence, missing fields, duplicate risk) land in a tracked Exceptions queue
6. Every record carries a full proof trail: reviewer name, notes, timestamp, approval tier
7. A weekly summary report is sent to the owner/bookkeeper

Architecture: n8n orchestrates all intake and routing via HTTP calls to the FastAPI backend. Reusable across clients by swapping credentials and approval thresholds only.

## Acceptance Criteria

### qclerq-invoice-pipeline.AC1: Invoice intake via all 3 paths
- **AC1.1 Success:** Gmail attachment PDF triggers workflow and reaches /extract
- **AC1.2 Success:** Drive folder new file triggers workflow and reaches /extract
- **AC1.3 Success:** Web upload form submission reaches /extract
- **AC1.4 Failure:** Non-PDF file attachment is ignored / skipped without error
- **AC1.5 Edge:** Duplicate PDF (same hash) detected before reaching extraction

### qclerq-invoice-pipeline.AC2: Claude extraction + validation
- **AC2.1 Success:** Valid PDF returns InvoiceExtracted with all required fields populated
- **AC2.2 Success:** confidence_overall reflects readability (low for scanned/blurry, high for clean digital PDF)
- **AC2.3 Success:** Math check passes when subtotal+tax+shipping-discount = total ±$0.02
- **AC2.4 Failure:** Math mismatch produces ExceptionItem with type "math_error"
- **AC2.5 Failure:** Missing required fields (invoice_number, vendor_raw, total, invoice_date) produce ExceptionItem list
- **AC2.6 Failure:** confidence_overall < 0.70 produces ExceptionItem and routes to Exceptions tab
- **AC2.7 Edge:** PDF.co fallback used when LlamaParse returns non-200

### qclerq-invoice-pipeline.AC3: 3-tier approval routing
- **AC3.1 Success:** Invoice total < $500 → approval_tier = "auto", no approval email sent
- **AC3.2 Success:** Invoice total $500–$5000 → approval_tier = "manager", approval email sent to manager_email
- **AC3.3 Success:** Invoice total > $5000 → approval_tier = "cfo", approval email sent to cfo_email
- **AC3.4 Success:** Approval callback records approved_by, approval_notes, approved_at in Sheets row
- **AC3.5 Failure:** No sync runs before approval_status = "approved" is recorded in Sheets

### qclerq-invoice-pipeline.AC4: Sync to all 3 targets with partial failure handling
- **AC4.1 Success:** Approved invoice creates row in Sheets Invoices tab with all fields
- **AC4.2 Success:** Approved invoice creates Bill in QuickBooks with correct VendorRef and Line items
- **AC4.3 Success:** Approved invoice creates expense in Jobber with correct job_id and total
- **AC4.4 Failure:** QB sync failure sets sync_status["quickbooks"] = "failed" without blocking Jobber sync
- **AC4.5 Failure:** Jobber sync failure sets sync_status["jobber"] = "failed" without blocking Sheets sync
- **AC4.6 Success:** Final sync_status dict written back to Sheets row reflects each target's outcome

### qclerq-invoice-pipeline.AC5: Vendor normalization
- **AC5.1 Success:** Known vendor (in vendors.json) resolves to canonical name without LLM call
- **AC5.2 Success:** Near-match vendor (typo/suffix variation) resolves via rapidfuzz at ≥88 score
- **AC5.3 Success:** Novel vendor resolved by LLM fallback and promoted to vendors.json
- **AC5.4 Edge:** vendor_normalized is never empty — falls back to vendor_raw if all layers fail

### qclerq-invoice-pipeline.AC6: Proof trail completeness
- **AC6.1 Success:** Every approved Sheets row has: approval_tier, approved_by, approval_notes, approved_at, sync_status, qb_bill_id, jobber_expense_id
- **AC6.2 Success:** Exception rows in Exceptions tab include issue_type, severity, message, status = "open"
- **AC6.3 Success:** file_hash stored on every row for future deduplication audit

### qclerq-invoice-pipeline.AC7: Weekly summary report
- **AC7.1 Success:** GET /report/weekly returns WeeklySummary with invoice_count, total_value, exception_rate, sync_failures, top_vendors
- **AC7.2 Success:** n8n Cron fires weekly and sends formatted report email to configured owner address
- **AC7.3 Edge:** Empty week (zero invoices) returns valid WeeklySummary with zeroed fields, no error

## Glossary

- **n8n**: A self-hosted workflow automation tool (similar to Zapier) used here as the orchestration layer — it triggers on Gmail/Drive/form events and calls the FastAPI backend via HTTP Request nodes.
- **FastAPI**: A Python web framework for building HTTP APIs. The backend service in this system; all extraction, validation, routing, and sync logic lives here.
- **Pydantic**: A Python data validation library. Used to define and enforce the shape of all data contracts (invoices, validation results, sync outcomes) via typed `BaseModel` classes.
- **BaseSettings**: Pydantic's settings-management class that loads config values from environment variables or `.env` files; used here so all per-client config is externalized.
- **LlamaParse**: A third-party PDF-to-text parsing service (by LlamaIndex). The primary PDF text extractor; PDF.co is used as fallback when LlamaParse returns a non-200 response.
- **PDF.co**: A document processing API used as the fallback PDF text extractor when LlamaParse fails.
- **QuickBooks Online (QBO)**: Intuit's cloud accounting platform. Approved invoices are created as Bills here via the `python-quickbooks` library.
- **python-quickbooks**: A Python client library for the QuickBooks Online REST API. `minorversion=75` is required; versions below 75 were deprecated in August 2025.
- **VendorRef**: The QuickBooks-specific object that links a Bill to a vendor record in QBO. Must be resolved from `vendor_normalized` before Bill creation — the vendor must already exist in QBO.
- **Jobber**: A field-service management platform (for contractors, trades, etc.). Approved invoices are pushed here as expenses via its GraphQL API.
- **GraphQL**: A query language and API protocol used by Jobber's API. There is no Python SDK; raw HTTP requests are made with a Bearer token and a versioned header (`X-JOBBER-GRAPHQL-VERSION`).
- **SHA-256 hash**: A cryptographic fingerprint of a file's contents. Used here for deduplication — the same PDF submitted twice produces the same hash, allowing the system to detect and flag duplicates before extraction runs.
- **confidence_overall**: A float (0.0–1.0) produced by Claude estimating how reliably it could read and extract the invoice. Scores below 0.70 are routed to the Exceptions queue rather than the approval flow.
- **approval_tier**: One of `auto`, `manager`, or `cfo` — determined by invoice total against configurable dollar thresholds. Controls whether human approval is required before sync runs.
- **ExceptionItem**: A structured record describing one validation problem (e.g., math mismatch, low confidence, missing field). A list of these is returned by the validation layer and written to the Exceptions tab in Sheets.
- **vendor_raw / vendor_normalized**: `vendor_raw` is the vendor name as Claude extracted it from the PDF. `vendor_normalized` is the canonical name after the three-layer matching cascade (lookup → fuzzy → LLM). Only `vendor_normalized` is used for downstream sync.
- **cleanco**: A Python library that strips legal entity suffixes (e.g., "LLC", "Inc.", "Corp.") from company names before fuzzy matching, improving match accuracy.
- **rapidfuzz**: A Python fuzzy string matching library. Used with token_sort_ratio ≥ 88 to match vendor names that have minor typos or suffix variations against the known vendor list.
- **vendors.json**: A local lookup file mapping raw vendor name variations to canonical names. Seeded with ~20 common entries; auto-updated when the LLM resolves a novel vendor.
- **proof trail**: The set of fields written to every approved Sheets row that document the full lifecycle: who approved, when, at what tier, and the sync outcome for each target system.
- **WeeklySummary**: The Pydantic model returned by `GET /report/weekly` — aggregates invoice count, total value, exception rate, per-target sync failures, and top vendors for the week.
- **n8n Form Trigger**: An n8n node type that exposes a hosted HTML form at a URL. Used here as the web upload intake path (`/submit-invoice`).
- **ExtractFromFile**: An n8n built-in node that reads file content (including PDFs) and extracts raw text — used in all three intake paths before calling the FastAPI backend.
- **minorversion=75**: A QuickBooks Online API version flag required on all requests. Versions below 75 were deprecated in August 2025; omitting it or using an older value will cause API failures.
- **60/30/10 ratio**: The project's orchestration design principle — 60% deterministic code, 30% rule-based logic, 10% LLM calls. Ensures reliability and cost control by reserving LLM use for cases where rules genuinely cannot substitute.

## Architecture

n8n orchestrates all intake and routing. Python FastAPI backend handles all intelligence and sync. No LLM calls inside n8n nodes.

### Intake (3 paths, all converge to /extract)

- **Gmail trigger:** polls for emails matching `has:attachment (invoice OR receipt OR bill) filename:pdf`, downloads attachment
- **Google Drive folder watch:** polls a specific folder for new files (Drive folder ID configured per client)
- **Web upload form:** n8n Form Trigger at `/submit-invoice`

All 3 paths: extract text via n8n `ExtractFromFile` node, compute SHA-256 hash via n8n Code node, then call `POST /extract`.

### FastAPI Backend Endpoints

```
POST /extract         → InvoiceExtracted
POST /validate        → ValidationResult (includes approval_tier: auto|manager|cfo)
POST /sync/sheets     → SyncResult partial
POST /sync/quickbooks → SyncResult partial
POST /sync/jobber     → SyncResult partial
GET  /report/weekly   → WeeklySummary
GET  /health          → {"status": "ok"}
```

### Pydantic Contracts (`src/app/schemas/invoice.py`)

```python
class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float
    category: Literal["materials","labor","software","utilities","rent","unknown"]

class InvoiceExtracted(BaseModel):
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    vendor_raw: str
    vendor_normalized: str          # filled by vendor_matcher cascade
    po_number: str | None
    job_id: str | None
    subtotal: float
    tax: float
    shipping: float
    discount: float
    total: float
    currency: str = "USD"
    line_items: list[LineItem]
    payment_terms: str | None
    confidence_overall: float       # 0.0–1.0
    duplicate_risk: Literal["none","possible","likely"]
    missing_required_fields: list[str]
    warnings: list[str]
    file_hash: str
    file_name: str

class ExceptionItem(BaseModel):
    type: str
    severity: Literal["low","medium","high"]
    message: str

class ValidationResult(BaseModel):
    is_clean: bool
    approval_tier: Literal["auto","manager","cfo"]
    exceptions: list[ExceptionItem]

class SyncResult(BaseModel):
    sheets_row_id: str | None
    qb_bill_id: str | None
    jobber_expense_id: str | None
    sync_status: dict[str, Literal["ok","failed","skipped"]]

class WeeklySummary(BaseModel):
    period_start: date
    period_end: date
    invoice_count: int
    total_value: float
    exception_rate: float
    sync_failures: dict[str, int]
    top_vendors: list[str]
```

### Config (`src/app/config.py`) — per-client reusability

```python
class Settings(BaseSettings):
    approval_tier_1_max: float = 500.0    # auto-approve below this
    approval_tier_2_max: float = 5000.0   # manager below this, CFO above
    confidence_threshold: float = 0.70
    manager_email: str
    cfo_email: str
    sheet_id: str
    # QB + Jobber OAuth tokens loaded from env
```

New client deployment = new `.env` file only. No code changes.

### Vendor Normalization Cascade (`src/app/services/vendor_matcher.py`)

Three-layer cascade in priority order:

1. **Exact/lookup match** — `vendors.json` dict lookup (O(1)), catches recurring known vendors
2. **Fuzzy match** — `cleanco` strips legal suffixes, `rapidfuzz` token_sort_ratio ≥ 88 against known vendor list
3. **LLM fallback** — Claude resolves ambiguous cases; result promoted back to `vendors.json` automatically

At steady state, LLM fires on <10% of invoices as the lookup table self-populates.

### Sync Layer — Sequential with Partial Failure

After approval callback from n8n:

```
POST /sync/sheets     → write Invoices row, record sheets_row_id
POST /sync/quickbooks → create Bill (python-quickbooks, minorversion=75)
POST /sync/jobber     → create expense (GraphQL, X-JOBBER-GRAPHQL-VERSION: 2025-04-16)
```

Each step runs inside a try/except. On failure: `sync_status[target] = "failed"`, continue to next target. Final `SyncResult` written back to the Sheets row (`sync_status` column). Partial failures are visible without blocking the other targets.

## Existing Patterns

- `workflows/n8n_invoice_desk.json` — 15-node skeleton already exists; Sheet ID `1Vy7dvq18Jh6CSoNBkNk1YMjN9btXGIsLv5nQdO7i9sY` already wired
- 60/30/10 orchestration ratio defined in `CLAUDE.md`: 60% deterministic, 30% rule-based, 10% LLM
- All Python service files named by purpose (no `utils.py`, `helpers.py`) per `CLAUDE.md` naming conventions
- Pydantic for all schema validation — `src/app/schemas/` directory already scaffolded
- No existing implementation patterns to follow — all service files are empty stubs

## Implementation Phases

<!-- START_PHASE_1 -->
### Phase 1: Schemas + Config Foundation

**Goal:** Define all data contracts and per-client configuration before any logic is written.

**Components:**
- `src/app/schemas/invoice.py` — `LineItem`, `InvoiceExtracted`, `ExceptionItem`, `ValidationResult`, `SyncResult`, `WeeklySummary`
- `src/app/schemas/validation.py` — field-level validation constants (required field list, confidence floor)
- `src/app/config.py` — `Settings` (BaseSettings): approval thresholds, emails, sheet ID, API keys
- `src/app/tests/test_schema.py` — golden tests: valid InvoiceExtracted round-trips, required field enforcement

**Dependencies:** None (first phase)

**Done when:** All Pydantic models importable, `test_schema.py` passes, `Settings` loads from a `.env.test`
<!-- END_PHASE_1 -->

<!-- START_PHASE_2 -->
### Phase 2: Extraction Pipeline

**Goal:** Parse a PDF and return a validated `InvoiceExtracted` from Claude.

**Components:**
- `src/app/services/parser_llamaparse.py` — LlamaParse HTTP call → raw text; raise on non-200
- `src/app/services/parser_pdfco.py` — PDF.co fallback; called when LlamaParse fails
- `src/app/services/extractor_claude.py` — sends raw text to Claude with `invoice_extraction.md` prompt, parses JSON response into `InvoiceExtracted`
- `src/app/prompts/invoice_extraction.md` — extraction prompt with field definitions, normalization rules, confidence scoring guidance
- `src/app/tests/test_extraction_goldens.py` — 2–3 real PDF fixtures; assert required fields populated and `confidence_overall` ≥ 0.7

**Dependencies:** Phase 1 (schemas)

**Done when:** Given a real PDF, `/extract` returns a valid `InvoiceExtracted` with all required fields; golden tests pass
<!-- END_PHASE_2 -->

<!-- START_PHASE_3 -->
### Phase 3: Vendor Normalization

**Goal:** Resolve raw vendor strings to canonical names via the 3-layer cascade.

**Components:**
- `src/app/services/vendor_matcher.py` — exact lookup → rapidfuzz (cleanco + token_sort_ratio ≥ 88) → Claude LLM fallback; promotes LLM results to `vendors.json`
- `src/app/data/vendors.json` — seed file with ~20 common vendor entries
- Tests inline in `test_extraction_goldens.py` — assert `vendor_normalized` is set and differs from raw for known cases

**Dependencies:** Phase 1 (schemas), Phase 2 (extractor calls vendor_matcher)

**Done when:** Known vendors resolve to canonical names without LLM; novel vendors fall through to LLM and are promoted to `vendors.json`
<!-- END_PHASE_3 -->

<!-- START_PHASE_4 -->
### Phase 4: Validation + Deduplication

**Goal:** Deterministic checks that catch bad data before it reaches the approval queue.

**Components:**
- `src/app/services/validator.py` — math check (±$0.02), required fields, confidence threshold, duplicate_risk flag → `ExceptionItem` list
- `src/app/services/dedupe.py` — SHA-256 hash lookup against `known_hashes` column in Sheets; returns `duplicate_risk`
- `src/app/tests/test_validation.py` — cases: math mismatch, missing invoice_number, low confidence, valid invoice
- `src/app/tests/test_dedupe.py` — cases: new hash (clean), known hash (duplicate)

**Dependencies:** Phase 1 (schemas)

**Done when:** `validator.py` and `dedupe.py` return correct `ValidationResult` for all test cases; both test files pass
<!-- END_PHASE_4 -->

<!-- START_PHASE_5 -->
### Phase 5: Approval Routing + FastAPI API

**Goal:** Wire all services into a running FastAPI backend with all endpoints operational.

**Components:**
- `src/app/services/approval_router.py` — `route(inv, settings) → Literal["auto","manager","cfo"]` using configurable thresholds
- `src/app/api.py` — all FastAPI route handlers: `/extract`, `/validate`, `/sync/sheets`, `/sync/quickbooks`, `/sync/jobber`, `/report/weekly`, `/health`
- `src/app/main.py` — app factory, startup lifespan, settings injection

**Dependencies:** Phases 1–4

**Done when:** `uvicorn src.app.main:app` starts without errors; `GET /health` returns 200; `POST /extract` and `POST /validate` return correct responses for a test invoice
<!-- END_PHASE_5 -->

<!-- START_PHASE_6 -->
### Phase 6: Sync Layer

**Goal:** Approved invoices sync to Sheets, QuickBooks, and Jobber with partial failure resilience.

**Components:**
- `src/app/services/sheets_sync.py` — write `Invoices` tab row + `Exceptions` tab row; update `sync_status` column; return `sheets_row_id`
- `src/app/services/quickbooks_sync.py` — create Bill via `python-quickbooks` (`minorversion=75`); VendorRef resolved from `vendor_normalized`; return `qb_bill_id`
- `src/app/services/jobber_sync.py` — `expenseCreate` GraphQL mutation via `requests`; header `X-JOBBER-GRAPHQL-VERSION: 2025-04-16`; return `jobber_expense_id`
- Sequential orchestration in `/sync/*` handlers: catch per-target exceptions, record `sync_status`, continue

**Dependencies:** Phase 5 (API running)

**Done when:** For a test invoice, all three sync targets write successfully; a simulated QB failure records `sync_status["quickbooks"] = "failed"` without blocking Jobber sync
<!-- END_PHASE_6 -->

<!-- START_PHASE_7 -->
### Phase 7: n8n Workflow Update + Proof Trail

**Goal:** Replace n8n Gemini extraction with Python backend calls and wire the full approval + proof trail flow.

**Components:**
- `workflows/n8n_invoice_desk.json` — remove `Invoice Parser AI Agent`, `Gemini Chat Model`, `Structured Output Parser` nodes; add HTTP Request node → `POST /extract`; add HTTP Request node → `POST /validate`; add 3-way IF split on `approval_tier`; fill 3 placeholders (Drive folder ID, approver email, finance email)
- Proof trail Sheets columns confirmed: `approval_tier`, `approved_by`, `approval_notes`, `approved_at`, `sync_status`, `qb_bill_id`, `jobber_expense_id`
- `workflows/CONTEXT.md` — update with credential re-attachment instructions post-import

**Dependencies:** Phase 5 (API endpoints live)

**Done when:** Full demo run via web upload form completes end-to-end: PDF → n8n → `/extract` → `/validate` → approval email → callback → `/sync/*` → Sheets row with all proof trail columns populated
<!-- END_PHASE_7 -->

<!-- START_PHASE_8 -->
### Phase 8: Weekly Report + Final Audit

**Goal:** Weekly summary report delivered via email; audit trail verified complete.

**Components:**
- `src/app/services/report_generator.py` — reads Sheets `Invoices` tab, aggregates: invoice count, total value, exception rate, sync failures by target, top 5 vendors
- `GET /report/weekly` endpoint in `src/app/api.py` — returns `WeeklySummary`
- n8n Cron node (already in skeleton) → `GET /report/weekly` → Gmail send to owner

**Dependencies:** Phase 6 (sync data in Sheets), Phase 7 (n8n workflow updated)

**Done when:** Cron node fires, report email received with correct aggregated data from the Sheets tab
<!-- END_PHASE_8 -->

## Additional Considerations

**Vendor normalization self-improvement:** LLM-resolved vendor matches are auto-promoted to `vendors.json`. LLM fallback should fire on <10% of invoices at steady state. Demo talking point: "the system gets smarter as you use it."

**QB auth gotchas:** `minorversion=75` required (versions <75 deprecated August 2025). Refresh token rotates in production — must persist after each call. Vendor must exist in QBO before Bill creation; `vendor_normalized` used to look up `VendorRef`.

**Jobber auth gotchas:** GraphQL only, no Python SDK. Raw `requests` with Bearer token. `expenseCreate` mutation input fields must be verified in Jobber GraphiQL (Developer Center → Manage Apps → Test in GraphiQL) before implementation — field names are not stable in public docs.

**Client reusability:** All per-client config lives in `.env`. No code changes between clients. This is the core sales reuse story.

**n8n credential re-attachment:** Credential references do not survive export/import between n8n instances. After importing `n8n_invoice_desk.json`, all Google OAuth, Gmail, and Sheets credentials must be re-attached manually in the n8n UI.

**Demo script:** Use the web upload form path (most visual). Drop a real PDF → watch n8n execute step-by-step → Sheets row appears → approval email arrives → click approve → sync_status columns fill in. Target: 60 seconds end-to-end.
