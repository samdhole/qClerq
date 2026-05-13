# qClerq Invoice Pipeline — Phase 8: Weekly Report + Final Audit

**Goal:** Weekly summary report delivered via email; audit trail verified complete.

**Architecture:** `report_generator.py` reads the Sheets `Invoices` and `Exceptions` tabs via gspread, aggregates metrics, and returns a `WeeklySummary`. `GET /report/weekly` wires it into the FastAPI backend. A new n8n Schedule Trigger node (weekly) calls `GET /report/weekly` and sends a formatted Gmail. No LLM calls.

**Tech Stack:** Python 3.11+, `gspread>=6.1.0`, `google-auth>=2.29.0`, FastAPI, n8n Schedule Trigger

**Scope:** Phase 8 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC7: Weekly summary report
- **qclerq-invoice-pipeline.AC7.1 Success:** GET /report/weekly returns WeeklySummary with invoice_count, total_value, exception_rate, sync_failures, top_vendors
- **qclerq-invoice-pipeline.AC7.2 Success:** n8n Cron fires weekly and sends formatted report email to configured owner address
- **qclerq-invoice-pipeline.AC7.3 Edge:** Empty week (zero invoices) returns valid WeeklySummary with zeroed fields, no error

---

## Discrepancy Notes

- `src/app/services/report_generator.py` does **not exist** as a stub — must be created.
- `src/app/api.py` is still completely empty — all Phase 5 route handlers were planned but not yet executed. Phase 8 assumes Phase 5 has been completed (api.py populated). If not, Task 1 must also complete the api.py `/report/weekly` stub.
- n8n Cron/Schedule Trigger node does **not exist** in the current workflow — design plan's claim it was "already in skeleton" was incorrect. Task 3 adds it.
- `exception_rate` is computed as: `exceptions_this_week / max(invoices_this_week, 1)`. Exceptions come from the `Exceptions` tab rows where `created_at` falls in the report period.
- `top_vendors`: top 5 `vendor_normalized` values by frequency in the `Invoices` tab for the period.
- `sync_failures`: count of rows per target where `sync_status` JSON contains `"failed"` for that target.
- Sheets row date filtering: use `invoice_date` column to scope to the report week. Parse as `date.fromisoformat()`.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Create src/app/services/report_generator.py

**Verifies:** qclerq-invoice-pipeline.AC7.1, qclerq-invoice-pipeline.AC7.3

**Files:**
- Create: `src/app/services/report_generator.py` (does not exist)

**Implementation:**

```python
from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import date, timedelta
from typing import Any

import gspread

from src.app.schemas.invoice import WeeklySummary


def _get_sheets_client(service_account_json: str) -> gspread.Client:
    info = json.loads(service_account_json)
    return gspread.service_account_from_dict(info)


def _parse_date(val: str) -> date | None:
    try:
        return date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _generate_sync(
    sheet_id: str,
    period_start: date,
    period_end: date,
    service_account_json: str,
) -> WeeklySummary:
    gc = _get_sheets_client(service_account_json)
    sh = gc.open_by_key(sheet_id)

    # Read Invoices tab
    inv_ws = sh.worksheet("Invoices")
    all_rows = inv_ws.get_all_records()

    # Filter to period
    period_rows = [
        r for r in all_rows
        if (d := _parse_date(str(r.get("invoice_date", "")))) and period_start <= d <= period_end
    ]

    invoice_count = len(period_rows)
    total_value = sum(float(r.get("total", 0) or 0) for r in period_rows)

    # Sync failures per target
    sync_failures: dict[str, int] = {"sheets": 0, "quickbooks": 0, "jobber": 0}
    for r in period_rows:
        raw = r.get("sync_status", "{}")
        try:
            status = json.loads(str(raw)) if raw else {}
        except json.JSONDecodeError:
            status = {}
        for target in sync_failures:
            if status.get(target) == "failed":
                sync_failures[target] += 1

    # Top vendors
    vendor_counts: Counter[str] = Counter(
        str(r.get("vendor_normalized", "")) for r in period_rows if r.get("vendor_normalized")
    )
    top_vendors = [vendor for vendor, _ in vendor_counts.most_common(5)]

    # Exception rate from Exceptions tab
    try:
        exc_ws = sh.worksheet("Exceptions")
        exc_rows = exc_ws.get_all_records()
        exc_in_period = [
            r for r in exc_rows
            if (d := _parse_date(str(r.get("created_at", "")[:10]))) and period_start <= d <= period_end
        ]
        exception_rate = len(exc_in_period) / max(invoice_count, 1)
    except Exception:
        exception_rate = 0.0

    return WeeklySummary(
        period_start=period_start,
        period_end=period_end,
        invoice_count=invoice_count,
        total_value=total_value,
        exception_rate=exception_rate,
        sync_failures=sync_failures,
        top_vendors=top_vendors,
    )


async def generate(settings: Any) -> WeeklySummary:
    """Generate weekly summary for the past 7 days."""
    today = date.today()
    period_start = today - timedelta(days=7)
    period_end = today - timedelta(days=1)

    return await asyncio.to_thread(
        _generate_sync,
        settings.sheet_id,
        period_start,
        period_end,
        settings.google_service_account_json,
    )
```

**Step 1: Create `src/app/services/report_generator.py` with the implementation above**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.report_generator import generate; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/report_generator.py
git commit -m "feat: implement weekly report generator reading from Sheets Invoices + Exceptions tabs"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Add /report/weekly tests

**Verifies:** qclerq-invoice-pipeline.AC7.1, qclerq-invoice-pipeline.AC7.3

**Files:**
- Create: `src/app/tests/test_report.py`

**What to test:**
- `AC7.1`: `generate()` returns `WeeklySummary` with all required fields when Sheets contains matching rows — mock gspread calls
- `AC7.3`: `generate()` with empty Sheets data (no rows) returns `WeeklySummary` with `invoice_count=0`, `total_value=0.0`, `exception_rate=0.0`, `sync_failures={"sheets":0,"quickbooks":0,"jobber":0}`, `top_vendors=[]`
- `GET /report/weekly` endpoint returns 200 with `WeeklySummary` JSON — use `client` fixture, mock `report_generator.generate`

Tests use `unittest.mock.patch` to mock `gspread.service_account_from_dict` — no real Sheets calls.

**Step 1: Create `src/app/tests/test_report.py`**

```python
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from src.app.schemas.invoice import WeeklySummary
from src.app.services.report_generator import _generate_sync


def make_sheets_row(**overrides) -> dict:
    today = date.today()
    base = {
        "invoice_date": str(today - timedelta(days=3)),
        "total": 500.0,
        "vendor_normalized": "Acme Corp",
        "sync_status": '{"sheets":"ok","quickbooks":"ok","jobber":"ok"}',
    }
    return {**base, **overrides}


def test_empty_week_returns_valid_summary():
    with patch("src.app.services.report_generator._get_sheets_client") as mock_gc:
        ws = MagicMock()
        ws.get_all_records.return_value = []
        sh = MagicMock()
        sh.worksheet.return_value = ws
        mock_gc.return_value.open_by_key.return_value = sh

        today = date.today()
        result = _generate_sync("test-id", today - timedelta(7), today, "{}")

    assert result.invoice_count == 0
    assert result.total_value == 0.0
    assert result.exception_rate == 0.0
    assert result.sync_failures == {"sheets": 0, "quickbooks": 0, "jobber": 0}
    assert result.top_vendors == []


# ... more tests
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_report.py -v
```

Expected: All tests pass.

**Step 3: Run full test suite**

```bash
pytest src/app/tests/ -v
```

Expected: All tests pass.

**Step 4: Commit**

```bash
git add src/app/tests/test_report.py
git commit -m "test: add weekly report tests for AC7.1 and AC7.3"
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Add n8n Schedule Trigger node and Gmail send to n8n_invoice_desk.json

**Verifies:** qclerq-invoice-pipeline.AC7.2

**Files:**
- Modify: `workflows/n8n_invoice_desk.json`

**Step 1: Add a Schedule Trigger node (weekly, Monday 9am)**

```json
{
  "id": "weekly-report-trigger",
  "name": "Weekly Report Trigger",
  "type": "n8n-nodes-base.scheduleTrigger",
  "typeVersion": 1,
  "position": [100, 700],
  "parameters": {
    "rule": {
      "interval": [
        {
          "field": "weeks",
          "weeklyConfig": {
            "day": "monday",
            "hour": 9,
            "minute": 0
          }
        }
      ]
    }
  }
}
```

**Step 2: Add HTTP Request node to call GET /report/weekly**

```json
{
  "id": "call-weekly-report",
  "name": "Get Weekly Report",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [300, 700],
  "parameters": {
    "method": "GET",
    "url": "http://localhost:8000/report/weekly",
    "options": {}
  }
}
```

Wire: `Weekly Report Trigger` → `Get Weekly Report`

**Step 3: Add Gmail node to send the report email**

```json
{
  "id": "send-weekly-report-email",
  "name": "Send Weekly Report Email",
  "type": "n8n-nodes-base.gmail",
  "typeVersion": 2,
  "position": [500, 700],
  "parameters": {
    "sendTo": "enigman.kk@gmail.com",
    "subject": "=Weekly Invoice Report — {{ new Date().toLocaleDateString() }}",
    "emailType": "text",
    "message": "=Weekly Invoice Report\n\nPeriod: {{ $json.period_start }} to {{ $json.period_end }}\nInvoices: {{ $json.invoice_count }}\nTotal Value: ${{ $json.total_value.toFixed(2) }}\nException Rate: {{ ($json.exception_rate * 100).toFixed(1) }}%\nSync Failures: {{ JSON.stringify($json.sync_failures) }}\nTop Vendors: {{ $json.top_vendors.join(', ') }}"
  }
}
```

Wire: `Get Weekly Report` → `Send Weekly Report Email`

**Step 4: Verify JSON valid**

```bash
python -c "import json; json.loads(open('workflows/n8n_invoice_desk.json').read()); print('valid JSON')"
```

**Step 5: Import updated workflow into n8n**

1. Open n8n UI → Settings → Import
2. Import updated `n8n_invoice_desk.json`
3. Re-attach Gmail OAuth2 credential to `Send Weekly Report Email` node
4. Activate the workflow

**Step 6: Test the weekly report manually**

Click "Test workflow" on the `Weekly Report Trigger` node. Verify:
- `GET /report/weekly` returns a valid `WeeklySummary` JSON
- Gmail send node fires and email arrives at `enigman.kk@gmail.com`
- Email body contains invoice_count, total_value, exception_rate, top_vendors

**Step 7: Commit**

```bash
git add workflows/n8n_invoice_desk.json
git commit -m "feat(n8n): add weekly Schedule Trigger + report email send (AC7.2)"
```
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-5) -->

<!-- START_TASK_4 -->
### Task 4: Full demo run — end-to-end verification

**Verifies:** All ACs across all phases

**Files:** None (operational verification)

**Step 1: Start backend**

```bash
uvicorn src.app.main:app --reload --host 127.0.0.1 --port 8000
```

**Step 2: Import final workflow into n8n and re-attach credentials**

Import `workflows/n8n_invoice_desk.json`. Re-attach:
- Google OAuth2 → `Invoice Folder Monitor`, `Download Invoice PDF`
- Gmail OAuth2 → `Monitor Gmail Invoices`, `Send Invoice for Approval`, `Send Rejection Notification`, `Send Weekly Report Email`
- Google Sheets OAuth2 → `Write to Invoices Sheet`, `Write to Exceptions Sheet`

**Step 3: Run the demo via web upload form (target: 60 seconds end-to-end)**

1. Open the web upload form URL (from `Web Upload Form` node)
2. Upload a real PDF invoice
3. Watch n8n execution step by step
4. Verify: `POST /extract` returns `InvoiceExtracted` with all fields
5. Verify: `POST /validate` returns correct `approval_tier`
6. For auto-approve path: verify `POST /sync` runs and Sheets row appears with proof trail
7. For manager/CFO path: verify approval email arrives; click approve; verify Sheets row updated

**Step 4: Audit the Sheets row (AC6)**

Open the Sheets spreadsheet and verify the created row has:
- `approval_tier`, `approved_by`, `approved_at` (AC6.1)
- `sync_status` dict with each target's outcome (AC4.6)
- `qb_bill_id`, `jobber_expense_id` (AC6.1)
- `file_hash` (AC6.3)

**Step 5: Test duplicate detection (AC1.5)**

Upload the same PDF again. Verify `duplicate_risk = "likely"` appears in the Exceptions tab.

**Step 6: Test low-confidence handling (AC2.6)**

Upload a low-resolution or handwritten PDF. Verify: if `confidence_overall < 0.70`, an exception row appears in the `Exceptions` tab with `status = "open"`.
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Run complete test suite and commit final state

**Verifies:** All phases

**Files:** None (verification + final commit)

**Step 1: Run all tests**

```bash
pytest src/app/tests/ -v --tb=short
```

Expected: All tests pass. Zero failures.

**Step 2: Run type check (optional but recommended)**

If mypy is available:
```bash
mypy src/app/ --ignore-missing-imports
```

**Step 3: Final commit**

```bash
git add -A
git status
git commit -m "feat: complete Phase 8 — weekly report, n8n schedule trigger, full demo verified"
```
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 8 Done When

- `GET /report/weekly` returns valid `WeeklySummary` (AC7.1)
- Empty week returns zeroed `WeeklySummary` without error (AC7.3)
- `pytest src/app/tests/ -v` passes all tests
- n8n Schedule Trigger fires and report email arrives at `enigman.kk@gmail.com` (AC7.2)
- Full demo run: PDF → n8n → extract → validate → sync → Sheets row with proof trail completes in ~60 seconds
