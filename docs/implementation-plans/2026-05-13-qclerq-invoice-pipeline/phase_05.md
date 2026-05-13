# qClerq Invoice Pipeline — Phase 5: Approval Routing + FastAPI API

**Goal:** Wire all services into a running FastAPI backend with all endpoints operational.

**Architecture:** `approval_router.py` provides pure `route()` logic; `validator.py` is updated to call it. `api.py` contains all route handlers wired to Phase 2–4 services. `main.py` is the app factory using `@asynccontextmanager` lifespan. Settings injected via `Depends(get_settings)` from `src/app/config.py`. Sync endpoints gate on an `approved_by` field — no sync without explicit approval record.

**Tech Stack:** Python 3.11+, FastAPI>=0.136.1, python-multipart>=0.0.9, httpx>=0.27.0, pytest with FastAPI TestClient

**Scope:** Phase 5 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC3: 3-tier approval routing
- **qclerq-invoice-pipeline.AC3.1 Success:** Invoice total < $500 → approval_tier = "auto", no approval email sent
- **qclerq-invoice-pipeline.AC3.2 Success:** Invoice total $500–$5000 → approval_tier = "manager"
- **qclerq-invoice-pipeline.AC3.3 Success:** Invoice total > $5000 → approval_tier = "cfo"
- **qclerq-invoice-pipeline.AC3.5 Failure:** No sync runs before approval_status = "approved" (gated by required `approved_by` field on sync requests)

---

## Discrepancy Notes

- Phase 4's `validator.py` was written with inline tier logic. Task 1 of this phase refactors it to delegate to `approval_router.route()` instead. This is a one-function change.
- CONTEXT.md uses `InvoiceRecord` — design plan uses `InvoiceExtracted`. Use `InvoiceExtracted` everywhere.
- CONTEXT.md lists only 5 endpoints; design plan has 7. Design plan is authoritative.
- Sync endpoints require an approval record (per CONTEXT.md: "no write without approval_token"). A separate `SyncRequest` model wraps `InvoiceExtracted` with approval fields (`approved_by`, `approval_notes`, `approved_at`). This satisfies AC3.4 and AC3.5.
- Use `@asynccontextmanager` lifespan — `@app.on_event` is deprecated.
- `ASGITransport` required for httpx AsyncClient tests (`app=app` is deprecated since httpx 0.27).
- No `conftest.py` exists — create one in `src/app/tests/` in this phase.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Implement src/app/services/approval_router.py and update validator.py

**Verifies:** qclerq-invoice-pipeline.AC3.1, qclerq-invoice-pipeline.AC3.2, qclerq-invoice-pipeline.AC3.3

**Files:**
- Modify: `src/app/services/approval_router.py` (currently empty stub)
- Modify: `src/app/services/validator.py` (update to call approval_router.route())

**Step 1: Implement approval_router.py**

```python
from __future__ import annotations

from typing import Literal


def route(total: float, tier_1_max: float, tier_2_max: float) -> Literal["auto", "manager", "cfo"]:
    """Determine approval tier from invoice total and configurable thresholds.

    Pure function — no I/O, no settings access.
    """
    if total < tier_1_max:
        return "auto"
    if total <= tier_2_max:
        return "manager"
    return "cfo"
```

**Step 2: Update validator.py to call approval_router.route()**

In `src/app/services/validator.py`, find the inline tier logic:
```python
    if inv.total < approval_tier_1_max:
        tier: Literal["auto", "manager", "cfo"] = "auto"
    elif inv.total <= approval_tier_2_max:
        tier = "manager"
    else:
        tier = "cfo"
```

Replace it with:
```python
    from src.app.services.approval_router import route as compute_tier
    tier = compute_tier(inv.total, approval_tier_1_max, approval_tier_2_max)
```

(Keep the import at the top of validator.py if you prefer — either way is fine.)

**Step 3: Verify**

```bash
python -c "from src.app.services.approval_router import route; print(route(100, 500, 5000))"
```

Expected: `auto`

```bash
python -c "from src.app.services.approval_router import route; print(route(1000, 500, 5000))"
```

Expected: `manager`

**Step 4: Run validation tests to confirm nothing broke**

```bash
pytest src/app/tests/test_validation.py -v
```

Expected: All tests pass (validator still computes correct tiers via approval_router).

**Step 5: Commit**

```bash
git add src/app/services/approval_router.py src/app/services/validator.py
git commit -m "feat: implement approval_router.route() and delegate from validator"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Add SyncRequest schema to src/app/schemas/invoice.py

**Verifies:** qclerq-invoice-pipeline.AC3.4, qclerq-invoice-pipeline.AC3.5

**Files:**
- Modify: `src/app/schemas/invoice.py` (add SyncRequest model at the bottom)

**Implementation:**

The sync endpoints need the invoice data plus the approval record. Add this to the bottom of `src/app/schemas/invoice.py`:

```python
from datetime import datetime


class SyncRequest(BaseModel):
    invoice: InvoiceExtracted
    approved_by: str
    approval_notes: str = ""
    approved_at: datetime
    approval_tier: Literal["auto", "manager", "cfo"]
```

**Step 1: Add `SyncRequest` to the bottom of `src/app/schemas/invoice.py`**

Also add `from datetime import datetime` at the top of the file alongside `from datetime import date`.

**Step 2: Verify importable**

```bash
python -c "from src.app.schemas.invoice import SyncRequest; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/schemas/invoice.py
git commit -m "feat: add SyncRequest schema with approval fields for sync endpoints"
```
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-5) -->

<!-- START_TASK_3 -->
### Task 3: Implement src/app/main.py and src/app/api.py (skeleton)

**Verifies:** None (infrastructure — verified by GET /health returning 200)

**Files:**
- Modify: `src/app/main.py` (currently empty stub)
- Modify: `src/app/api.py` (currently empty stub)

**Step 1: Implement src/app/main.py**

Use `@asynccontextmanager` lifespan. Do NOT create `Settings()` in lifespan — settings are provided via `Depends(get_settings)` (lru_cache singleton). Creating a second instance in lifespan causes env var loading to differ between the lifespan and handler contexts.

```python
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.app.api import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AI Invoice Desk", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
```

**Step 2: Implement src/app/api.py (all endpoints)**

Key wiring in this step:
- `/extract`: fetch known hashes from Sheets, run `dedupe.check_duplicate()`, set `result.duplicate_risk` before returning (AC1.5)
- `/validate`: if `validation.is_clean == False`, call `sheets_sync.write_exceptions()` to populate Exceptions tab (AC6.2)
- `/approval-callback`: receives n8n `sendAndWait` approval payload, calls `POST /sync` flow (AC3.4)
- Sync service calls use lazy imports to avoid circular imports at startup

```python
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from src.app.config import Settings, get_settings
from src.app.schemas.invoice import (
    InvoiceExtracted,
    SyncRequest,
    SyncResult,
    ValidationResult,
    WeeklySummary,
)
from src.app.services import extractor_claude, validator as inv_validator
from src.app.services.dedupe import check_duplicate, compute_hash

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/extract")
async def extract_invoice(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceExtracted:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail=f"Only PDF files accepted, got: {file.content_type}")
    pdf_bytes = await file.read()
    file_hash = compute_hash(pdf_bytes)
    file_name = file.filename or "invoice.pdf"

    result = extractor_claude.parse_and_extract(
        pdf_bytes=pdf_bytes,
        file_hash=file_hash,
        file_name=file_name,
        llama_api_key=settings.llama_cloud_api_key,
        pdfco_api_key=settings.pdfco_api_key,
        anthropic_api_key=settings.anthropic_api_key,
    )
    if result is None:
        raise HTTPException(status_code=422, detail="Extraction failed — could not parse PDF")

    from src.app.services.vendor_matcher import normalize
    result.vendor_normalized = normalize(result.vendor_raw, settings.anthropic_api_key)

    # Dedupe check: fetch known hashes from Sheets and flag duplicates (AC1.5).
    try:
        from src.app.services.sheets_sync import get_known_hashes
        known = await asyncio.to_thread(
            get_known_hashes, settings.sheet_id, settings.google_service_account_json
        )
        result.duplicate_risk = check_duplicate(file_hash, known)
    except Exception:
        pass  # best-effort — do not block extraction if Sheets is unreachable

    return result


@router.post("/validate")
async def validate_invoice(
    invoice: InvoiceExtracted,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ValidationResult:
    validation = inv_validator.validate(
        invoice,
        settings.approval_tier_1_max,
        settings.approval_tier_2_max,
    )

    # Write exception rows for any issues found (AC6.2).
    if not validation.is_clean and validation.exceptions:
        try:
            from src.app.services.sheets_sync import write_exceptions
            await asyncio.to_thread(
                write_exceptions,
                settings.sheet_id,
                invoice,
                validation.exceptions,
                settings.google_service_account_json,
            )
        except Exception:
            pass  # best-effort — validation result is still returned to caller

    return validation


@router.post("/approval-callback")
async def approval_callback(
    req: SyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Receive n8n sendAndWait approval payload and trigger full sync (AC3.4).

    n8n sends approved_by, approval_notes, approved_at from the sendAndWait
    resume payload. This endpoint validates that approved_by is non-empty
    (the sync gating requirement per CONTEXT.md) and delegates to sync_all.
    """
    if not req.approved_by:
        raise HTTPException(status_code=422, detail="approved_by is required to sync")
    # Delegate to the combined sync flow defined in Phase 6.
    # NOTE: sync_all raises 501 until Phase 6 Task 4 replaces its body — this endpoint will also 501 until then.
    from src.app.api import sync_all
    return await sync_all(req, settings)


@router.post("/sync")
async def sync_all(
    req: SyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Run all sync targets. Implementation populated in Phase 6 Task 4."""
    from src.app.services import sheets_sync, quickbooks_sync, jobber_sync  # noqa: F401
    raise HTTPException(status_code=501, detail="Sync not yet implemented — complete Phase 6")


@router.get("/report/weekly")
async def weekly_report(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WeeklySummary:
    from src.app.services import report_generator
    return await report_generator.generate(settings)
```

Note: `/sync` body is replaced in Phase 6 Task 4 with the full orchestration. The stub raises 501 so the server starts cleanly without importing uninitialised sync modules.

**Step 3: Verify server starts and /health returns 200**

```bash
uvicorn src.app.main:app --reload --host 127.0.0.1 --port 8000
```

In a separate terminal:
```bash
curl http://127.0.0.1:8000/health
```

Expected: `{"status":"ok"}`

Stop the server with Ctrl+C.

**Step 4: Commit**

```bash
git add src/app/main.py src/app/api.py
git commit -m "feat: implement FastAPI app factory and all route handlers"
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Create src/app/tests/conftest.py with shared fixtures

**Verifies:** None (test infrastructure)

**Files:**
- Create: `src/app/tests/conftest.py` (does not exist)

**Implementation:**

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.app.main import create_app


@pytest.fixture(scope="session")
def test_settings_env(tmp_path_factory):
    env_file = tmp_path_factory.mktemp("env") / ".env.test"
    env_file.write_text(
        "APPROVAL_TIER_1_MAX=500.0\n"
        "APPROVAL_TIER_2_MAX=5000.0\n"
        "CONFIDENCE_THRESHOLD=0.70\n"
        "MANAGER_EMAIL=manager@test.local\n"
        "CFO_EMAIL=cfo@test.local\n"
        "SHEET_ID=test-sheet-id\n"
        "LLAMA_CLOUD_API_KEY=test-llama\n"
        "PDFCO_API_KEY=test-pdfco\n"
        "ANTHROPIC_API_KEY=test-anthropic\n"
        "QB_CLIENT_ID=test-qb-id\n"
        "QB_CLIENT_SECRET=test-qb-secret\n"
        "QB_REFRESH_TOKEN=test-refresh\n"
        "QB_REALM_ID=test-realm\n"
        "JOBBER_ACCESS_TOKEN=test-jobber\n"
        "GOOGLE_SERVICE_ACCOUNT_JSON={}\n"
    )
    return str(env_file)


@pytest.fixture(scope="session")
def client(test_settings_env):
    from src.app.config import Settings, get_settings

    def override_settings():
        return Settings(_env_file=test_settings_env)

    app = create_app()
    app.dependency_overrides[get_settings] = override_settings
    with TestClient(app) as c:
        yield c
```

**Step 1: Create `src/app/tests/conftest.py` with the content above**

**Step 2: Verify conftest loads without error**

```bash
pytest src/app/tests/ --collect-only
```

Expected: No import errors; all test files collected.

**Step 3: Commit**

```bash
git add src/app/tests/conftest.py
git commit -m "test: add conftest.py with shared TestClient fixture"
```
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Add API endpoint tests — /health and /extract

**Verifies:** qclerq-invoice-pipeline.AC3.1, qclerq-invoice-pipeline.AC3.2, qclerq-invoice-pipeline.AC3.3 (via /validate), qclerq-invoice-pipeline.AC3.5 (sync blocked without approval fields)

**Files:**
- Create: `src/app/tests/test_api.py` (new file)

**What to test:**
- `GET /health` returns `{"status": "ok"}` with 200
- `POST /extract` with valid PDF bytes (mocked `parse_and_extract`) returns `InvoiceExtracted`
- `POST /validate` with valid `InvoiceExtracted` JSON returns `ValidationResult` with correct `approval_tier`
  - `AC3.1`: total < 500 → `approval_tier = "auto"`
  - `AC3.2`: total = 1000 → `approval_tier = "manager"`
  - `AC3.3`: total = 10000 → `approval_tier = "cfo"`
- `POST /sync/sheets` without `approved_by` field returns 422 (schema validation)
- `POST /validate` with math error → `is_clean=False`, exceptions include `"math_error"`

Use the `client` fixture from conftest.py. Mock `extractor_claude.parse_and_extract` using `unittest.mock.patch`.

**Step 1: Create src/app/tests/test_api.py**

```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import date

from src.app.schemas.invoice import InvoiceExtracted


def make_extracted(**overrides) -> dict:
    defaults = {
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-15",
        "vendor_raw": "Acme Corp LLC",
        "vendor_normalized": "Acme Corp",
        "subtotal": 100.0,
        "tax": 8.0,
        "shipping": 0.0,
        "discount": 0.0,
        "total": 108.0,
        "line_items": [],
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "abc123",
        "file_name": "test.pdf",
    }
    return {**defaults, **overrides}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_validate_auto_tier(client):
    r = client.post("/validate", json=make_extracted(total=100.0, subtotal=92.0, tax=8.0))
    assert r.status_code == 200
    data = r.json()
    assert data["approval_tier"] == "auto"


def test_validate_manager_tier(client):
    r = client.post("/validate", json=make_extracted(
        total=1000.0, subtotal=920.0, tax=80.0
    ))
    assert r.status_code == 200
    assert r.json()["approval_tier"] == "manager"


def test_validate_cfo_tier(client):
    r = client.post("/validate", json=make_extracted(
        total=10000.0, subtotal=9000.0, tax=1000.0
    ))
    assert r.status_code == 200
    assert r.json()["approval_tier"] == "cfo"


# ... additional test functions
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_api.py -v
```

Expected: All tests pass (health + validate endpoints are fully wired; sync endpoints return 500 until Phase 6).

**Step 3: Run full test suite**

```bash
pytest src/app/tests/ -v
```

Expected: All tests pass.

**Step 4: Commit**

```bash
git add src/app/tests/test_api.py
git commit -m "test: add API endpoint tests for /health, /validate tier routing (AC3.1-3.3)"
```
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 5 Done When

- `uvicorn src.app.main:app` starts without errors
- `GET /health` returns `{"status": "ok"}`
- `POST /validate` returns correct `approval_tier` for amounts below/between/above thresholds
- `pytest src/app/tests/ -v` passes (all prior + new tests)
