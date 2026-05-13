# qClerq Invoice Pipeline — Phase 1: Schemas + Config Foundation

**Goal:** Define all data contracts and per-client configuration before any logic is written.

**Architecture:** Pure Pydantic v2 models in `src/app/schemas/invoice.py`; per-client settings via `pydantic-settings` BaseSettings in `src/app/config.py`; validation constants in `src/app/schemas/validation.py`. No logic, no LLM calls, no I/O in this phase.

**Tech Stack:** Python 3.11+, Pydantic v2 (`pydantic>=2.13.4`), pydantic-settings (`pydantic-settings>=2.14.1`), pytest (`pytest>=9.0.3`), FastAPI (`fastapi>=0.136.1`)

**Scope:** Phase 1 of 8 (foundation — no ACs directly, enables all downstream phases)

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

This phase is infrastructure + schema foundation. It implements no named acceptance criteria directly.
All files are currently empty stubs. This phase populates them.

**Verifies: None** (no numbered AC cases — schemas are verified by importability and round-trip tests)

---

## Discrepancy Notes

- `src/app/CONTEXT.md` uses `InvoiceRecord`; design plan uses `InvoiceExtracted`. **Use `InvoiceExtracted`** — design plan is the authoritative spec. If `InvoiceRecord` appears in future context, it refers to the same type.
- `src/app/CONTEXT.md` specifies math tolerance as `$0.02 OR 2%, whichever is larger`. The design plan states only `$0.02`. **Use the CONTEXT.md rule** in `validator.py` (Phase 4). Schemas in this phase carry no math validation logic.
- `src/app/models.py` exists as an empty stub not mentioned in the design. Leave it empty — do not populate or delete it in this phase.
- `pyproject.toml` is completely empty — Phase 1 must populate it before any `import` works.
- `.env.test` does not exist — Phase 1 creates it.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Populate pyproject.toml with all project dependencies

**Files:**
- Modify: `pyproject.toml` (currently 0 bytes — replace entirely)

**Step 1: Write pyproject.toml**

Replace the empty file with:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ai-invoice-desk"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.136.1",
    "uvicorn[standard]>=0.46.0",
    "pydantic>=2.13.4",
    "pydantic-settings>=2.14.1",
    "anthropic>=0.101.0",
    "httpx>=0.27.0",
    "python-multipart>=0.0.9",
    "rapidfuzz>=3.9.0",
    "cleanco>=2.2",
    "requests>=2.32.0",
    "gspread>=6.1.0",
    "google-auth>=2.29.0",
    "python-quickbooks>=0.9.12",
    "intuitlib>=2.3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=9.0.3",
    "pytest-asyncio>=1.3.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["src/app/tests"]

[tool.hatch.build.targets.wheel]
packages = ["src/app"]
```

**Step 2: Install dependencies**

```bash
pip install -e ".[dev]"
```

Expected: Installs without errors. All packages resolve.

**Step 3: Verify Python can find the package**

```bash
python -c "import fastapi; import pydantic; import pydantic_settings; print('ok')"
```

Expected: prints `ok`

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: populate pyproject.toml with all project dependencies"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Create .env.example and .env.test

**Files:**
- Modify: `.env.example` (currently empty stub — populate with all required keys)
- Create: `.env.test` (does not exist)

**Step 1: Populate .env.example**

```bash
# .env.example — copy to .env and fill in real values
APPROVAL_TIER_1_MAX=500.0
APPROVAL_TIER_2_MAX=5000.0
CONFIDENCE_THRESHOLD=0.70
MANAGER_EMAIL=manager@example.com
CFO_EMAIL=cfo@example.com
SHEET_ID=your-google-sheet-id-here
LLAMA_CLOUD_API_KEY=llc-...
PDFCO_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
QB_CLIENT_ID=...
QB_CLIENT_SECRET=...
QB_REFRESH_TOKEN=...
QB_REALM_ID=...
JOBBER_ACCESS_TOKEN=...
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
```

**Step 2: Create .env.test with test-safe values**

```bash
APPROVAL_TIER_1_MAX=500.0
APPROVAL_TIER_2_MAX=5000.0
CONFIDENCE_THRESHOLD=0.70
MANAGER_EMAIL=manager@test.local
CFO_EMAIL=cfo@test.local
SHEET_ID=test-sheet-id
LLAMA_CLOUD_API_KEY=test-llama-key
PDFCO_API_KEY=test-pdfco-key
ANTHROPIC_API_KEY=test-anthropic-key
QB_CLIENT_ID=test-qb-client-id
QB_CLIENT_SECRET=test-qb-secret
QB_REFRESH_TOKEN=test-refresh-token
QB_REALM_ID=test-realm-id
JOBBER_ACCESS_TOKEN=test-jobber-token
GOOGLE_SERVICE_ACCOUNT_JSON={}
```

**Step 3: Verify .env.test is gitignored**

```bash
grep -n "\.env" .gitignore
```

Expected: `.env` and `.env.test` are listed (or `.env*` pattern covers them).
If `.env.test` is NOT gitignored, add it: `echo ".env.test" >> .gitignore`

**Step 4: Commit**

```bash
git add .env.example .gitignore
git commit -m "chore: populate .env.example with all required keys, add .env.test for testing"
```

Note: Do NOT `git add .env.test` — it must stay gitignored.
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-6) -->

<!-- START_TASK_3 -->
### Task 3: Implement src/app/schemas/invoice.py

**Verifies:** None (schema round-trip verified by Task 6 tests)

**Files:**
- Modify: `src/app/schemas/invoice.py` (currently empty stub)

**Implementation:**

```python
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float
    category: Literal["materials", "labor", "software", "utilities", "rent", "unknown"]


class InvoiceExtracted(BaseModel):
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    vendor_raw: str
    vendor_normalized: str
    po_number: str | None = None
    job_id: str | None = None
    subtotal: float
    tax: float
    shipping: float
    discount: float
    total: float
    currency: str = "USD"
    line_items: list[LineItem]
    payment_terms: str | None = None
    confidence_overall: float
    duplicate_risk: Literal["none", "possible", "likely"]
    missing_required_fields: list[str]
    warnings: list[str]
    file_hash: str
    file_name: str


class ExceptionItem(BaseModel):
    type: str
    severity: Literal["low", "medium", "high"]
    message: str


class ValidationResult(BaseModel):
    is_clean: bool
    approval_tier: Literal["auto", "manager", "cfo"]
    exceptions: list[ExceptionItem]


class SyncResult(BaseModel):
    sheets_row_id: str | None = None
    qb_bill_id: str | None = None
    jobber_expense_id: str | None = None
    sync_status: dict[str, Literal["ok", "failed", "skipped"]]


class WeeklySummary(BaseModel):
    period_start: date
    period_end: date
    invoice_count: int
    total_value: float
    exception_rate: float
    sync_failures: dict[str, int]
    top_vendors: list[str]
```

**Step 1: Write the implementation above to `src/app/schemas/invoice.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.schemas.invoice import InvoiceExtracted, ExceptionItem, ValidationResult, SyncResult, WeeklySummary, LineItem; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/schemas/invoice.py
git commit -m "feat: implement InvoiceExtracted and related Pydantic schemas"
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Implement src/app/schemas/validation.py

**Verifies:** None

**Files:**
- Modify: `src/app/schemas/validation.py` (currently empty stub)

**Implementation:**

```python
from __future__ import annotations

REQUIRED_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "vendor_raw",
    "total",
    "invoice_date",
)

CONFIDENCE_FLOOR: float = 0.70

MATH_TOLERANCE_ABS: float = 0.02

MATH_TOLERANCE_REL: float = 0.02
```

Note: Math check uses `max(MATH_TOLERANCE_ABS, total * MATH_TOLERANCE_REL)` per `src/app/CONTEXT.md`. These constants are defined here; the actual check lives in `validator.py` (Phase 4).

**Step 1: Write the implementation to `src/app/schemas/validation.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.schemas.validation import REQUIRED_FIELDS, CONFIDENCE_FLOOR; print(REQUIRED_FIELDS)"
```

Expected: prints the tuple of required field names

**Step 3: Commit**

```bash
git add src/app/schemas/validation.py
git commit -m "feat: add validation constants (required fields, confidence floor, math tolerance)"
```
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Implement src/app/config.py

**Verifies:** None (Settings loading verified by Task 6 tests)

**Files:**
- Modify: `src/app/config.py` (currently empty stub)

**Implementation:**

```python
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    approval_tier_1_max: float = 500.0
    approval_tier_2_max: float = 5000.0
    confidence_threshold: float = 0.70

    manager_email: str
    cfo_email: str
    sheet_id: str

    llama_cloud_api_key: str = ""
    pdfco_api_key: str = ""
    anthropic_api_key: str

    qb_client_id: str = ""
    qb_client_secret: str = ""
    qb_refresh_token: str = ""
    qb_realm_id: str = ""

    jobber_access_token: str = ""

    google_service_account_json: str = "{}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Step 1: Write the implementation to `src/app/config.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.config import Settings; print('ok')"
```

Expected: prints `ok` (does not raise even though required fields have no values — they'll raise at instantiation, not import)

**Step 3: Commit**

```bash
git add src/app/config.py
git commit -m "feat: implement Settings via pydantic-settings BaseSettings"
```
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Implement src/app/tests/test_schema.py and verify all tests pass

**Verifies:** None (no numbered ACs — validates schema contracts for downstream phases)

**Files:**
- Modify: `src/app/tests/test_schema.py` (currently empty stub)

**What to test:**
- `InvoiceExtracted` round-trips correctly (construct → model_dump → reconstruct)
- Required fields (`vendor_raw`, `vendor_normalized`, `subtotal`, `tax`, `shipping`, `discount`, `total`, `currency`, `file_hash`, `file_name`, `confidence_overall`, `duplicate_risk`, `line_items`, `missing_required_fields`, `warnings`) are enforced — missing one raises `ValidationError`
- Optional fields (`invoice_number`, `invoice_date`, `due_date`, `po_number`, `job_id`, `payment_terms`) accept `None`
- `LineItem.category` rejects invalid literals
- `ExceptionItem.severity` rejects invalid literals
- `ValidationResult.approval_tier` rejects invalid literals
- `SyncResult.sync_status` accepts `{"sheets": "ok", "quickbooks": "failed"}`
- `WeeklySummary` round-trips with all fields populated
- `Settings` loads from `.env.test` correctly with `Settings(_env_file=".env.test")`

Follow project test patterns: pytest functions, no classes, no mocking needed (pure Pydantic validation). The test file at `src/app/tests/test_schema.py` already exists as an empty stub — implement it.

**Step 1: Implement test_schema.py**

Write pytest functions that test each contract above. Tests are pure — no I/O, no external services.

Example structure:
```python
import pytest
from pydantic import ValidationError
from datetime import date

from src.app.schemas.invoice import (
    InvoiceExtracted, LineItem, ExceptionItem,
    ValidationResult, SyncResult, WeeklySummary,
)
from src.app.config import Settings


def make_line_item(**overrides) -> dict:
    base = {
        "description": "Widget",
        "quantity": 2.0,
        "unit_price": 50.0,
        "line_total": 100.0,
        "category": "materials",
    }
    return {**base, **overrides}


def make_invoice(**overrides) -> dict:
    base = {
        "vendor_raw": "Acme Corp LLC",
        "vendor_normalized": "Acme Corp",
        "subtotal": 100.0,
        "tax": 8.0,
        "shipping": 0.0,
        "discount": 0.0,
        "total": 108.0,
        "line_items": [make_line_item()],
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "abc123",
        "file_name": "invoice.pdf",
    }
    return {**base, **overrides}


# ... test functions below
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_schema.py -v
```

Expected: All tests pass, no errors.

**Step 3: Commit**

```bash
git add src/app/tests/test_schema.py
git commit -m "test: add golden tests for all invoice Pydantic schemas"
```
<!-- END_TASK_6 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 1 Done When

- `python -c "from src.app.schemas.invoice import InvoiceExtracted"` exits 0
- `pytest src/app/tests/test_schema.py -v` passes
- `python -c "from src.app.config import Settings; s = Settings(_env_file='.env.test'); print(s.manager_email)"` prints `manager@test.local`
