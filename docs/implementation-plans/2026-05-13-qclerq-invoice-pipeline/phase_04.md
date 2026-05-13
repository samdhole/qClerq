# qClerq Invoice Pipeline — Phase 4: Validation + Deduplication

**Goal:** Deterministic checks that catch bad data before it reaches the approval queue.

**Architecture:** `validator.py` is pure Python — no I/O, no LLM — takes an `InvoiceExtracted` and returns a `ValidationResult` with an `ExceptionItem` list. `dedupe.py` is also pure — takes a `file_hash` and a `set[str]` of known hashes (provided by the caller from Sheets) and returns a `duplicate_risk` literal. Keeping both modules stateless makes them trivially testable and ensures no silent failures.

**Tech Stack:** Python 3.11+, stdlib only (`hashlib` for SHA-256 utility), Pydantic v2 (schemas from Phase 1)

**Scope:** Phase 4 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC1: Invoice intake via all 3 paths
- **qclerq-invoice-pipeline.AC1.5 Edge:** Duplicate PDF (same hash) detected before reaching extraction

### qclerq-invoice-pipeline.AC2: Claude extraction + validation
- **qclerq-invoice-pipeline.AC2.3 Success:** Math check passes when subtotal+tax+shipping-discount = total ±$0.02
- **qclerq-invoice-pipeline.AC2.4 Failure:** Math mismatch produces ExceptionItem with type "math_error"
- **qclerq-invoice-pipeline.AC2.5 Failure:** Missing required fields (invoice_number, vendor_raw, total, invoice_date) produce ExceptionItem list
- **qclerq-invoice-pipeline.AC2.6 Failure:** confidence_overall < 0.70 produces ExceptionItem and routes to Exceptions tab

---

## Discrepancy Notes

- `src/app/schemas/validation.py` is still empty (Phase 1 plan is written but not yet executed). The constants (`REQUIRED_FIELDS`, `CONFIDENCE_FLOOR`, `MATH_TOLERANCE_ABS`, `MATH_TOLERANCE_REL`) must exist before `validator.py` can import them. Task 1 of this phase verifies this precondition.
- Math tolerance per `src/app/CONTEXT.md`: `max(MATH_TOLERANCE_ABS, total * MATH_TOLERANCE_REL)` — i.e., `max(0.02, total * 0.02)`, whichever is larger. The design plan says only `±$0.02` but CONTEXT.md is authoritative.
- `dedupe.py` takes `known_hashes: set[str]` as a parameter (not a Sheets client). The caller (Phase 5 API layer) fetches known hashes from Sheets and passes them in. This keeps `dedupe.py` pure and testable without a Sheets mock.
- `file_hash` computation: `hashlib.sha256(pdf_bytes).hexdigest()` — done by the n8n workflow before calling `/extract`. The hash is passed in as a field on `InvoiceExtracted`. `dedupe.py` does NOT compute hashes — it only checks them.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Verify validation constants exist (precondition check)

**Verifies:** None (precondition)

**Files:**
- Read: `src/app/schemas/validation.py`

**Step 1: Check that Phase 1 populated validation.py**

```bash
python -c "from src.app.schemas.validation import REQUIRED_FIELDS, CONFIDENCE_FLOOR, MATH_TOLERANCE_ABS, MATH_TOLERANCE_REL; print('ok')"
```

**If this fails (ImportError or empty file):** Phase 1 has not been executed yet. Write the following to `src/app/schemas/validation.py` before proceeding:

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

Then commit:
```bash
git add src/app/schemas/validation.py
git commit -m "feat: add validation constants (required fields, confidence floor, math tolerance)"
```

**If import succeeds:** Skip writing — constants already exist. Continue to Task 2.
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement src/app/services/validator.py

**Verifies:** qclerq-invoice-pipeline.AC2.3, qclerq-invoice-pipeline.AC2.4, qclerq-invoice-pipeline.AC2.5, qclerq-invoice-pipeline.AC2.6

**Files:**
- Modify: `src/app/services/validator.py` (currently empty stub)

**Implementation:**

```python
from __future__ import annotations

from typing import Literal

from src.app.schemas.invoice import ExceptionItem, InvoiceExtracted, ValidationResult
from src.app.schemas.validation import (
    CONFIDENCE_FLOOR,
    MATH_TOLERANCE_ABS,
    MATH_TOLERANCE_REL,
    REQUIRED_FIELDS,
)


def validate(inv: InvoiceExtracted, approval_tier_1_max: float, approval_tier_2_max: float) -> ValidationResult:
    """Run deterministic validation checks. Returns ValidationResult with all exceptions found.

    Pure function — no I/O, no LLM calls, no side effects.
    """
    exceptions: list[ExceptionItem] = []

    # Math check: subtotal + tax + shipping - discount == total (within tolerance)
    calculated = inv.subtotal + inv.tax + inv.shipping - inv.discount
    tolerance = max(MATH_TOLERANCE_ABS, abs(inv.total) * MATH_TOLERANCE_REL)
    if abs(calculated - inv.total) > tolerance:
        exceptions.append(ExceptionItem(
            type="math_error",
            severity="high",
            message=(
                f"Math mismatch: {inv.subtotal} + {inv.tax} + {inv.shipping} - "
                f"{inv.discount} = {calculated:.2f}, but total = {inv.total:.2f} "
                f"(tolerance ±{tolerance:.2f})"
            ),
        ))

    # Missing required fields
    for field in REQUIRED_FIELDS:
        value = getattr(inv, field, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            exceptions.append(ExceptionItem(
                type="missing_required_field",
                severity="high",
                message=f"Required field '{field}' is missing or empty",
            ))

    # Low confidence
    if inv.confidence_overall < CONFIDENCE_FLOOR:
        exceptions.append(ExceptionItem(
            type="low_confidence",
            severity="medium",
            message=(
                f"confidence_overall {inv.confidence_overall:.2f} is below "
                f"threshold {CONFIDENCE_FLOOR}"
            ),
        ))

    # Duplicate risk
    if inv.duplicate_risk in ("possible", "likely"):
        severity: Literal["low", "medium", "high"] = (
            "high" if inv.duplicate_risk == "likely" else "medium"
        )
        exceptions.append(ExceptionItem(
            type="duplicate_risk",
            severity=severity,
            message=f"Invoice flagged as duplicate_risk='{inv.duplicate_risk}'",
        ))

    # Determine approval tier
    if inv.total < approval_tier_1_max:
        tier: Literal["auto", "manager", "cfo"] = "auto"
    elif inv.total <= approval_tier_2_max:
        tier = "manager"
    else:
        tier = "cfo"

    is_clean = len(exceptions) == 0
    return ValidationResult(
        is_clean=is_clean,
        approval_tier=tier,
        exceptions=exceptions,
    )
```

**Step 1: Write the implementation above to `src/app/services/validator.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.validator import validate; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/validator.py
git commit -m "feat: implement deterministic invoice validator (math, required fields, confidence)"
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Implement src/app/services/dedupe.py

**Verifies:** qclerq-invoice-pipeline.AC1.5

**Files:**
- Modify: `src/app/services/dedupe.py` (currently empty stub)

**Implementation:**

```python
from __future__ import annotations

import hashlib
from typing import Literal


def compute_hash(pdf_bytes: bytes) -> str:
    """Compute SHA-256 hex digest of PDF bytes."""
    return hashlib.sha256(pdf_bytes).hexdigest()


def check_duplicate(
    file_hash: str,
    known_hashes: set[str],
) -> Literal["none", "possible", "likely"]:
    """Check file_hash against known_hashes set.

    Returns:
        "likely"   — hash is an exact match (same file submitted before)
        "none"     — hash not found in known_hashes
    """
    if file_hash in known_hashes:
        return "likely"
    return "none"
```

Note: `possible` is reserved for future soft-duplicate detection (same vendor + total + ±3 day window per CONTEXT.md). For now, only exact hash match is implemented.

**Step 1: Write the implementation above to `src/app/services/dedupe.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.dedupe import compute_hash, check_duplicate; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/dedupe.py
git commit -m "feat: implement SHA-256 deduplication check"
```
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-5) -->

<!-- START_TASK_4 -->
### Task 4: Implement src/app/tests/test_validation.py

**Verifies:** qclerq-invoice-pipeline.AC2.3, qclerq-invoice-pipeline.AC2.4, qclerq-invoice-pipeline.AC2.5, qclerq-invoice-pipeline.AC2.6

**Files:**
- Modify: `src/app/tests/test_validation.py` (currently empty stub)

**What to test:**
- `AC2.3`: Invoice where `subtotal + tax + shipping - discount == total` → `is_clean=True`, no math exception
- `AC2.4`: Invoice where totals don't match → `exceptions` contains `ExceptionItem(type="math_error")` 
- `AC2.4`: Math check respects 2% tolerance: e.g. total=$1000, calculated=$980 (2% diff = $20 ≥ $20, edge case)
- `AC2.5`: Invoice missing `invoice_number` (None) → exceptions contain `type="missing_required_field"` for that field
- `AC2.5`: Invoice missing all 4 required fields → 4 missing field exceptions
- `AC2.6`: `confidence_overall=0.65` (below 0.70) → exceptions contain `type="low_confidence"`
- `AC2.6`: `confidence_overall=0.70` (at threshold) → no low_confidence exception
- Valid clean invoice → `is_clean=True`, `approval_tier` set correctly based on total

**Step 1: Implement test_validation.py**

The file exists as an empty stub — implement it. Create a `make_invoice()` helper (or import from test_schema.py pattern) to build `InvoiceExtracted` instances with valid defaults that can be overridden per test.

```python
import pytest
from src.app.schemas.invoice import InvoiceExtracted
from src.app.services.validator import validate

TIER1_MAX = 500.0
TIER2_MAX = 5000.0

def make_invoice(**overrides) -> InvoiceExtracted:
    defaults = {
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
        "file_name": "invoice.pdf",
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-15",
    }
    return InvoiceExtracted.model_validate({**defaults, **overrides})

# ... test functions
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_validation.py -v
```

Expected: All tests pass.

**Step 3: Commit**

```bash
git add src/app/tests/test_validation.py
git commit -m "test: add validator tests for math check, missing fields, confidence threshold (AC2.3-2.6)"
```
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Implement src/app/tests/test_dedupe.py

**Verifies:** qclerq-invoice-pipeline.AC1.5

**Files:**
- Modify: `src/app/tests/test_dedupe.py` (currently empty stub)

**What to test:**
- `AC1.5`: `check_duplicate(hash, {hash})` returns `"likely"` — same hash detected
- `AC1.5`: `check_duplicate(hash, set())` returns `"none"` — new hash is clean
- `compute_hash(bytes)` returns a 64-character hex string (SHA-256)
- `compute_hash` is deterministic: same bytes → same hash
- `compute_hash` is hash-sensitive: different bytes → different hash

**Step 1: Implement test_dedupe.py**

```python
from src.app.services.dedupe import check_duplicate, compute_hash

def test_new_hash_is_clean():
    assert check_duplicate("abc123", set()) == "none"

def test_known_hash_is_likely_duplicate():
    h = "deadbeef"
    assert check_duplicate(h, {h}) == "likely"

def test_compute_hash_returns_hex_string():
    h = compute_hash(b"some pdf bytes")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)

def test_compute_hash_is_deterministic():
    data = b"invoice data"
    assert compute_hash(data) == compute_hash(data)

def test_compute_hash_differs_for_different_bytes():
    assert compute_hash(b"file A") != compute_hash(b"file B")
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_dedupe.py -v
```

Expected: All 5 tests pass.

**Step 3: Run full test suite to confirm nothing broken**

```bash
pytest src/app/tests/ -v
```

Expected: All tests across all test files pass.

**Step 4: Commit**

```bash
git add src/app/tests/test_dedupe.py
git commit -m "test: add deduplication tests (AC1.5)"
```
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

---

## Phase 4 Done When

- `pytest src/app/tests/test_validation.py -v` passes (AC2.3–2.6 covered)
- `pytest src/app/tests/test_dedupe.py -v` passes (AC1.5 covered)
- `pytest src/app/tests/ -v` passes (all phases so far)
- `validate()` returns correct `ValidationResult` for all test cases
- `check_duplicate()` returns `"likely"` for known hash, `"none"` for new hash
