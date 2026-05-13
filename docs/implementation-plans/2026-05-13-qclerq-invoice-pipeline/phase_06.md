# qClerq Invoice Pipeline — Phase 6: Sync Layer

**Goal:** Approved invoices sync to Google Sheets, QuickBooks, and Jobber with partial failure resilience.

**Architecture:** Three independent sync modules (`sheets_sync.py`, `quickbooks_sync.py`, `jobber_sync.py`), each called sequentially from the API layer. Each is wrapped in try/except — on failure, `sync_status[target] = "failed"` is set and execution continues. Final `SyncResult` is written back to the Sheets row. gspread writes to `Invoices` tab; Exceptions land in `Exceptions` tab.

**Tech Stack:** Python 3.11+, `gspread>=6.1.0`, `google-auth>=2.29.0`, `python-quickbooks>=0.9.12` (NOT `quickbooks-python`), `requests>=2.32.0`, `intuitlib`

**Scope:** Phase 6 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC4: Sync to all 3 targets with partial failure handling
- **qclerq-invoice-pipeline.AC4.1 Success:** Approved invoice creates row in Sheets Invoices tab with all fields
- **qclerq-invoice-pipeline.AC4.2 Success:** Approved invoice creates Bill in QuickBooks with correct VendorRef and Line items
- **qclerq-invoice-pipeline.AC4.3 Success:** Approved invoice creates expense in Jobber with correct job_id and total
- **qclerq-invoice-pipeline.AC4.4 Failure:** QB sync failure sets sync_status["quickbooks"] = "failed" without blocking Jobber sync
- **qclerq-invoice-pipeline.AC4.5 Failure:** Jobber sync failure sets sync_status["jobber"] = "failed" without blocking Sheets sync
- **qclerq-invoice-pipeline.AC4.6 Success:** Final sync_status dict written back to Sheets row

### qclerq-invoice-pipeline.AC6: Proof trail completeness
- **qclerq-invoice-pipeline.AC6.1 Success:** Every approved Sheets row has: approval_tier, approved_by, approval_notes, approved_at, sync_status, qb_bill_id, jobber_expense_id
- **qclerq-invoice-pipeline.AC6.2 Success:** Exception rows in Exceptions tab include issue_type, severity, message, status = "open"
- **qclerq-invoice-pipeline.AC6.3 Success:** file_hash stored on every row for future deduplication audit

---

## CRITICAL Package Name Discrepancy

**Phase 1 plan wrote `quickbooks-python>=0.9.3` to `pyproject.toml` — this is the WRONG package.**

The correct package is `python-quickbooks` (ej2/python-quickbooks, v0.9.12). `quickbooks-python` is a different, unmaintained package by simonv3.

**Fix before implementing QB sync:**
1. Remove `quickbooks-python>=0.9.3` from `pyproject.toml`
2. Add `python-quickbooks>=0.9.12` and `intuitlib>=2.3.0`
3. Re-run `pip install -e ".[dev]"` to install the correct package

---

## Discrepancy Notes

- Sheet ID confirmed: `1Vy7dvq18Jh6CSoNBkNk1YMjN9btXGIsLv5nQdO7i9sY` (from n8n workflow JSON). It must be added to `.env` / `.env.test` as `SHEET_ID`.
- **Jobber `expenseCreate` mutation input fields are NOT in public docs** — must be verified in Jobber GraphiQL before writing the mutation. See Task 3 for step-by-step verification instructions.
- `GOOGLE_SERVICE_ACCOUNT_JSON` is stored as a JSON string in env — must be parsed with `json.loads()` before passing to `gspread.service_account_from_dict()`.
- `gspread` uses **1-based** row indexing for `update_cell`.
- QB `minorversion=75` is required — versions below 75 deprecated August 2025.
- The `api.py` sync handlers (Phase 5) use `await sheets_sync.sync(req, settings)` — these modules must be async-compatible. Wrap blocking gspread/QB/Jobber calls using `asyncio.to_thread()`.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Fix pyproject.toml QB package and implement src/app/services/sheets_sync.py

**Verifies:** qclerq-invoice-pipeline.AC4.1, qclerq-invoice-pipeline.AC4.6, qclerq-invoice-pipeline.AC6.1, qclerq-invoice-pipeline.AC6.2, qclerq-invoice-pipeline.AC6.3

**Files:**
- Modify: `pyproject.toml` (fix QB package name)
- Modify: `src/app/services/sheets_sync.py` (currently empty stub)

**Step 1: Fix pyproject.toml QB package**

In `pyproject.toml` under `[project] dependencies`, replace:
```toml
"quickbooks-python>=0.9.3",
```
With:
```toml
"python-quickbooks>=0.9.12",
"intuitlib>=2.3.0",
```

Run `pip install -e ".[dev]"` to install the correct packages.

**Step 2: Implement sheets_sync.py**

```python
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import gspread

from src.app.schemas.invoice import ExceptionItem, InvoiceExtracted, SyncRequest, SyncResult

_INVOICES_SHEET = "Invoices"
_EXCEPTIONS_SHEET = "Exceptions"

INVOICE_COLUMNS = [
    "file_hash", "file_name", "invoice_number", "invoice_date", "due_date",
    "vendor_raw", "vendor_normalized", "po_number", "job_id",
    "subtotal", "tax", "shipping", "discount", "total", "currency",
    "line_items", "payment_terms", "confidence_overall", "duplicate_risk",
    "missing_required_fields", "warnings",
    "approval_tier", "approval_status", "approved_by", "approval_notes", "approved_at",
    "sync_status", "qb_bill_id", "jobber_expense_id",
]

EXCEPTION_COLUMNS = [
    "file_hash", "file_name", "vendor_normalized", "invoice_number",
    "issue_type", "severity", "message", "status", "created_at",
]


def _get_client(service_account_json: str) -> gspread.Client:
    info = json.loads(service_account_json)
    return gspread.service_account_from_dict(info)


def _inv_to_row(req: SyncRequest, qb_bill_id: str | None, jobber_expense_id: str | None, sync_status: dict) -> list[Any]:
    inv = req.invoice
    return [
        inv.file_hash,
        inv.file_name,
        inv.invoice_number or "",
        str(inv.invoice_date) if inv.invoice_date else "",
        str(inv.due_date) if inv.due_date else "",
        inv.vendor_raw,
        inv.vendor_normalized,
        inv.po_number or "",
        inv.job_id or "",
        inv.subtotal,
        inv.tax,
        inv.shipping,
        inv.discount,
        inv.total,
        inv.currency,
        json.dumps([item.model_dump() for item in inv.line_items]),
        inv.payment_terms or "",
        inv.confidence_overall,
        inv.duplicate_risk,
        json.dumps(inv.missing_required_fields),
        json.dumps(inv.warnings),
        req.approval_tier,
        "approved",
        req.approved_by,
        req.approval_notes,
        req.approved_at.isoformat(),
        json.dumps(sync_status),
        qb_bill_id or "",
        jobber_expense_id or "",
    ]


def _write_invoice_row(
    sheet_id: str,
    req: SyncRequest,
    qb_bill_id: str | None,
    jobber_expense_id: str | None,
    sync_status: dict,
    service_account_json: str,
) -> str:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    row = _inv_to_row(req, qb_bill_id, jobber_expense_id, sync_status)
    assert len(row) == len(INVOICE_COLUMNS), f"Row has {len(row)} values but INVOICE_COLUMNS has {len(INVOICE_COLUMNS)}"
    ws.append_row(row, value_input_option="USER_ENTERED")
    all_rows = ws.get_all_values()
    row_index = len(all_rows)
    return str(row_index)


def _update_sync_status(sheet_id: str, row_index: int, sync_status: dict, qb_bill_id: str | None, jobber_expense_id: str | None, service_account_json: str) -> None:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    sync_col = INVOICE_COLUMNS.index("sync_status") + 1
    qb_col = INVOICE_COLUMNS.index("qb_bill_id") + 1
    jobber_col = INVOICE_COLUMNS.index("jobber_expense_id") + 1
    ws.update_cell(row_index, sync_col, json.dumps(sync_status))
    if qb_bill_id:
        ws.update_cell(row_index, qb_col, qb_bill_id)
    if jobber_expense_id:
        ws.update_cell(row_index, jobber_col, jobber_expense_id)


def write_exceptions(sheet_id: str, inv: InvoiceExtracted, exceptions: list[ExceptionItem], service_account_json: str) -> None:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_EXCEPTIONS_SHEET)
    now = datetime.now(timezone.utc).isoformat()
    for exc in exceptions:
        row = [
            inv.file_hash,
            inv.file_name,
            inv.vendor_normalized,
            inv.invoice_number or "",
            exc.type,
            exc.severity,
            exc.message,
            "open",
            now,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")


def get_known_hashes(sheet_id: str, service_account_json: str) -> set[str]:
    """Return all file_hash values from the Invoices tab for duplicate detection."""
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    hash_col = INVOICE_COLUMNS.index("file_hash") + 1
    values = ws.col_values(hash_col)
    return {v for v in values[1:] if v}  # skip header row


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Write invoice row to Sheets Invoices tab. Returns SyncResult with sheets_row_id."""
    sync_status: dict[str, str] = {"sheets": "ok"}
    try:
        row_id = await asyncio.to_thread(
            _write_invoice_row,
            settings.sheet_id,
            req,
            None,
            None,
            sync_status,
            settings.google_service_account_json,
        )
    except Exception as exc:
        sync_status["sheets"] = "failed"
        row_id = None

    return SyncResult(
        sheets_row_id=row_id,
        sync_status=sync_status,
    )
```

**Step 3: Verify importable**

```bash
python -c "from src.app.services.sheets_sync import sync; print('ok')"
```

Expected: prints `ok`

**Step 4: Commit**

```bash
git add pyproject.toml src/app/services/sheets_sync.py
git commit -m "feat: implement Google Sheets sync with proof trail columns (AC4.1, AC6.1-6.3)"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement src/app/services/quickbooks_sync.py

**Verifies:** qclerq-invoice-pipeline.AC4.2, qclerq-invoice-pipeline.AC4.4

**Files:**
- Modify: `src/app/services/quickbooks_sync.py` (currently empty stub)

**Implementation:**

```python
from __future__ import annotations

import asyncio
from typing import Any

from src.app.schemas.invoice import SyncRequest, SyncResult


def _create_bill_sync(req: SyncRequest, settings: Any) -> str:
    """Create QB Bill and return qb_bill_id. Raises on failure."""
    from quickbooks import QuickBooks
    from quickbooks.objects.base import Ref
    from quickbooks.objects.bill import Bill
    from quickbooks.objects.detailline import AccountBasedExpenseLine, AccountBasedExpenseLineDetail
    from quickbooks.objects.vendor import Vendor
    from intuitlib.client import AuthClient

    auth_client = AuthClient(
        client_id=settings.qb_client_id,
        client_secret=settings.qb_client_secret,
        redirect_uri="https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl",
        environment="production",
    )
    qb = QuickBooks(
        auth_client=auth_client,
        refresh_token=settings.qb_refresh_token,
        company_id=settings.qb_realm_id,
        minorversion=75,
    )

    # Look up vendor by normalized name
    vendor_name = req.invoice.vendor_normalized
    vendors = Vendor.where(f"DisplayName = '{vendor_name}'", qb=qb)
    if not vendors:
        raise ValueError(f"Vendor '{vendor_name}' not found in QuickBooks")

    vendor_ref = Ref()
    vendor_ref.value = vendors[0].Id
    vendor_ref.name = vendor_name

    lines = []
    for item in req.invoice.line_items:
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = Ref()
        detail.AccountRef.value = "1"

        line = AccountBasedExpenseLine()
        line.Amount = item.line_total
        line.DetailType = "AccountBasedExpenseLineDetail"
        line.AccountBasedExpenseLineDetail = detail
        line.Description = item.description
        lines.append(line)

    if not lines:
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = Ref()
        detail.AccountRef.value = "1"
        line = AccountBasedExpenseLine()
        line.Amount = req.invoice.total
        line.DetailType = "AccountBasedExpenseLineDetail"
        line.AccountBasedExpenseLineDetail = detail
        line.Description = f"Invoice {req.invoice.invoice_number or 'unknown'}"
        lines = [line]

    bill = Bill()
    bill.VendorRef = vendor_ref
    bill.Line = lines
    if req.invoice.invoice_date:
        bill.TxnDate = str(req.invoice.invoice_date)

    bill.save(qb=qb)
    return str(bill.Id)


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Create QuickBooks Bill. Returns SyncResult with qb_bill_id."""
    try:
        qb_bill_id = await asyncio.to_thread(_create_bill_sync, req, settings)
        sync_status = {"quickbooks": "ok"}
    except Exception:
        qb_bill_id = None
        sync_status = {"quickbooks": "failed"}

    return SyncResult(qb_bill_id=qb_bill_id, sync_status=sync_status)
```

**Step 1: Write implementation to `src/app/services/quickbooks_sync.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.quickbooks_sync import sync; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/quickbooks_sync.py
git commit -m "feat: implement QuickBooks Bill creation sync (AC4.2, AC4.4)"
```
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-5) -->

<!-- START_TASK_3 -->
### Task 3: Verify Jobber expenseCreate mutation schema, then implement src/app/services/jobber_sync.py

**Verifies:** qclerq-invoice-pipeline.AC4.3, qclerq-invoice-pipeline.AC4.5

**Files:**
- Modify: `src/app/services/jobber_sync.py` (currently empty stub)

**REQUIRED FIRST: Verify expenseCreate input fields in Jobber GraphiQL**

The `expenseCreate` mutation input fields are **not in public documentation** and must be verified before writing code.

1. Go to: Jobber Developer Center → Manage Apps → your app → "Test in GraphiQL"
2. In the Documentation tab (book icon), search for `ExpenseCreateInput`
3. Note the exact field names, types, and required fields
4. Specifically confirm:
   - Is the amount field named `total`, `amount`, or something else?
   - Is the job reference field `jobId` (EncodedId), `job` (object), or other?
   - What is the date field format and name?
   - Is `description` available?

**Run this introspection query in GraphiQL to get authoritative schema:**

```graphql
{
  __type(name: "ExpenseCreateInput") {
    name
    inputFields {
      name
      type {
        name
        kind
        ofType { name kind }
      }
      defaultValue
    }
  }
}
```

Record the exact field names from the result. Then implement based on verified schema.

**Implementation (adjust field names based on GraphiQL verification):**

```python
from __future__ import annotations

import asyncio
from typing import Any

import requests

from src.app.schemas.invoice import SyncRequest, SyncResult

_JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
_JOBBER_API_VERSION = "2025-04-16"

# IMPORTANT: Verify these field names in Jobber GraphiQL before use.
# The mutation and input type field names below are based on Jobber data model docs
# and must be confirmed against live schema via introspection.
_EXPENSE_CREATE_MUTATION = """
mutation ExpenseCreate($input: ExpenseCreateInput!) {
    expenseCreate(input: $input) {
        expense {
            id
            description
            total
        }
        userErrors { message path }
    }
}
"""


def _create_expense_sync(req: SyncRequest, access_token: str) -> str:
    """Create Jobber expense and return jobber_expense_id. Raises on failure."""
    inv = req.invoice

    # VERIFY THESE FIELD NAMES: must match ExpenseCreateInput from introspection
    expense_input: dict[str, Any] = {
        "description": f"Invoice {inv.invoice_number or 'unknown'} from {inv.vendor_normalized}",
        "total": inv.total,
    }
    if inv.job_id:
        expense_input["jobId"] = inv.job_id
    if inv.invoice_date:
        expense_input["date"] = str(inv.invoice_date)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-JOBBER-GRAPHQL-VERSION": _JOBBER_API_VERSION,
    }
    payload = {
        "query": _EXPENSE_CREATE_MUTATION,
        "variables": {"input": expense_input},
    }

    resp = requests.post(_JOBBER_GRAPHQL_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Jobber GraphQL errors: {data['errors']}")

    result = data.get("data", {}).get("expenseCreate", {})
    user_errors = result.get("userErrors", [])
    if user_errors:
        raise RuntimeError(f"Jobber userErrors: {user_errors}")

    expense = result.get("expense")
    if not expense:
        raise RuntimeError("Jobber expenseCreate returned no expense object")

    return str(expense["id"])


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Create Jobber expense. Returns SyncResult with jobber_expense_id."""
    try:
        jobber_expense_id = await asyncio.to_thread(
            _create_expense_sync, req, settings.jobber_access_token
        )
        sync_status = {"jobber": "ok"}
    except Exception:
        jobber_expense_id = None
        sync_status = {"jobber": "failed"}

    return SyncResult(jobber_expense_id=jobber_expense_id, sync_status=sync_status)
```

**Step 1: Verify ExpenseCreateInput schema in Jobber GraphiQL (see instructions above)**

**Step 2: Adjust field names in `_expense_input` dict if they differ from the implementation above**

**Step 3: Write the verified implementation to `src/app/services/jobber_sync.py`**

**Step 4: Verify importable**

```bash
python -c "from src.app.services.jobber_sync import sync; print('ok')"
```

Expected: prints `ok`

**Step 5: Commit**

```bash
git add src/app/services/jobber_sync.py
git commit -m "feat: implement Jobber expense creation sync (AC4.3, AC4.5)"
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Wire sync orchestration into api.py and add partial-failure test

**Verifies:** qclerq-invoice-pipeline.AC4.4, qclerq-invoice-pipeline.AC4.5, qclerq-invoice-pipeline.AC4.6

**Files:**
- Modify: `src/app/api.py` — update sync handlers to run all three sequentially and merge sync_status
- Create: `src/app/tests/test_sync.py`

**Step 1: Update the sync handlers in api.py to run sequentially with partial failure**

Replace the three sync route handler stubs in `src/app/api.py` with a combined orchestration endpoint at `POST /sync`. The proof trail requirement (AC4.6, AC6.1) demands that the Sheets row is written **first** with initial status, then `_update_sync_status` backfills `qb_bill_id`, `jobber_expense_id`, and final `sync_status` after QB + Jobber complete.

Find the three sync handlers in `api.py` (`sync_sheets`, `sync_quickbooks`, `sync_jobber`) and also add a `POST /sync` endpoint:

```python
@router.post("/sync")
async def sync_all(
    req: SyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Sync to all three targets. Sheets row written first; backfilled after QB + Jobber."""
    import asyncio
    from src.app.services import sheets_sync, quickbooks_sync, jobber_sync

    sheets_row_id: str | None = None
    qb_bill_id: str | None = None
    jobber_expense_id: str | None = None
    sync_status: dict[str, str] = {"sheets": "pending", "quickbooks": "pending", "jobber": "pending"}

    # 1. Write Sheets row first — establishes the audit record before any sync attempt.
    try:
        sheets_row_id = await asyncio.to_thread(
            sheets_sync._write_invoice_row,
            settings.sheet_id,
            req,
            None,
            None,
            sync_status,
            settings.google_service_account_json,
        )
        sync_status["sheets"] = "ok"
    except Exception:
        sync_status["sheets"] = "failed"

    # 2. QB sync — failure does not block Jobber (AC4.4).
    try:
        qb_result = await quickbooks_sync.sync(req, settings)
        qb_bill_id = qb_result.qb_bill_id
        sync_status["quickbooks"] = qb_result.sync_status.get("quickbooks", "ok")
    except Exception:
        sync_status["quickbooks"] = "failed"

    # 3. Jobber sync — failure does not affect QB or Sheets (AC4.5).
    try:
        jobber_result = await jobber_sync.sync(req, settings)
        jobber_expense_id = jobber_result.jobber_expense_id
        sync_status["jobber"] = jobber_result.sync_status.get("jobber", "ok")
    except Exception:
        sync_status["jobber"] = "failed"

    # 4. Backfill Sheets row with final IDs and sync_status (AC4.6, AC6.1).
    if sheets_row_id is not None:
        try:
            await asyncio.to_thread(
                sheets_sync._update_sync_status,
                settings.sheet_id,
                int(sheets_row_id),
                sync_status,
                qb_bill_id,
                jobber_expense_id,
                settings.google_service_account_json,
            )
        except Exception:
            pass  # best-effort — row already exists, partial data is better than nothing

    return SyncResult(
        sheets_row_id=sheets_row_id,
        qb_bill_id=qb_bill_id,
        jobber_expense_id=jobber_expense_id,
        sync_status=sync_status,
    )
```

**Step 2: Create src/app/tests/test_sync.py**

```python
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

import pytest

from src.app.schemas.invoice import SyncRequest, SyncResult
from src.app.services.sheets_sync import sync as sheets_sync
from src.app.services.quickbooks_sync import sync as qb_sync
from src.app.services.jobber_sync import sync as jobber_sync


def make_sync_request(**overrides) -> dict:
    base = {
        "invoice": {
            "vendor_raw": "Acme Corp LLC",
            "vendor_normalized": "Acme Corp",
            "subtotal": 100.0, "tax": 8.0, "shipping": 0.0, "discount": 0.0,
            "total": 108.0, "line_items": [], "confidence_overall": 0.95,
            "duplicate_risk": "none", "missing_required_fields": [],
            "warnings": [], "file_hash": "abc123", "file_name": "invoice.pdf",
            "invoice_number": "INV-001", "invoice_date": "2026-01-15",
        },
        "approved_by": "manager@test.local",
        "approval_notes": "Approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approval_tier": "auto",
    }
    return {**base, **overrides}
```

Tests must verify (AC4.4): when QB sync raises, `sync_status["quickbooks"] == "failed"` and Jobber sync still runs.
Tests must verify (AC4.5): when Jobber sync raises, `sync_status["jobber"] == "failed"` and Sheets sync result is unaffected.
Tests must verify (AC4.6): the combined `POST /sync` endpoint returns a `SyncResult` with all three targets' statuses.

Write these tests via the `client` fixture (from conftest.py) with mocked sync modules.

**Step 3: Run tests**

```bash
pytest src/app/tests/test_sync.py -v
```

Expected: All tests pass.

**Step 4: Commit**

```bash
git add src/app/api.py src/app/tests/test_sync.py
git commit -m "feat: wire sequential sync orchestration with partial failure handling (AC4.4-4.6)"
```
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Run full test suite and verify phase complete

**Verifies:** All Phase 6 ACs

**Files:** None (verification only)

**Step 1: Run full test suite**

```bash
pytest src/app/tests/ -v
```

Expected: All tests pass.

**Step 2: Verify server still starts**

```bash
uvicorn src.app.main:app --reload --host 127.0.0.1 --port 8000 &
curl http://127.0.0.1:8000/health
kill %1
```

Expected: `{"status":"ok"}`

**Step 3: Commit if any loose changes**

```bash
git status
# Commit any uncommitted changes
```
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 6 Done When

- `pytest src/app/tests/test_sync.py -v` passes (AC4.4 + AC4.5 partial failure tests)
- QB sync failure → `sync_status["quickbooks"] = "failed"` without blocking Jobber (AC4.4)
- Jobber sync failure → `sync_status["jobber"] = "failed"` without blocking Sheets (AC4.5)
- `SyncResult.sync_status` dict reflects all three targets' outcomes (AC4.6)
- Sheets row includes `approval_tier`, `approved_by`, `approved_at`, `sync_status`, `qb_bill_id`, `jobber_expense_id`, `file_hash` (AC6.1, AC6.3)
