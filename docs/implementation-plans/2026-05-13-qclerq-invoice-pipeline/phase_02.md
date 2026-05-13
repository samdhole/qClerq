# qClerq Invoice Pipeline — Phase 2: Extraction Pipeline

**Goal:** Parse a PDF and return a validated `InvoiceExtracted` from Claude.

**Architecture:** `parser_llamaparse.py` → raw text (with `parser_pdfco.py` as fallback) → `extractor_claude.py` → `InvoiceExtracted`. All HTTP calls are isolated in their respective modules. The extractor uses Claude `tool_use` with a forced tool call so the response is always a parsed dict (no `json.loads` needed). Extractors never raise on parse failure — they return `None` on total failure.

**Tech Stack:** Python 3.11+, `anthropic>=0.101.0`, `httpx>=0.27.0`, `requests>=2.32.0`, Pydantic v2, pytest with `unittest.mock`

**Scope:** Phase 2 of 8

**Codebase verified:** 2026-05-13

---

## Acceptance Criteria Coverage

### qclerq-invoice-pipeline.AC2: Claude extraction + validation
- **qclerq-invoice-pipeline.AC2.1 Success:** Valid PDF returns InvoiceExtracted with all required fields populated
- **qclerq-invoice-pipeline.AC2.2 Success:** confidence_overall reflects readability (low for scanned/blurry, high for clean digital PDF)
- **qclerq-invoice-pipeline.AC2.7 Edge:** PDF.co fallback used when LlamaParse returns non-200

---

## Discrepancy Notes

- `src/app/CONTEXT.md` calls the return type `InvoiceRecord | None`; this plan uses `InvoiceExtracted | None` (the canonical name from `src/app/schemas/invoice.py` as set in Phase 1). These are the same type.
- `extractor_openai.py` exists as an in-scope stub (per CONTEXT.md). It is out of scope for Phase 2 (only `extractor_claude.py` is specified). Leave it as an empty stub.
- LlamaParse v2 API is async (upload → create job → poll). v1 is deprecated. Use v2.
- PDF.co uses `x-api-key` header (NOT `Authorization: Bearer`).
- Claude extraction uses `tool_use` with `tool_choice={"type": "tool", "name": "extract_invoice"}` to force a structured response. No `json.loads` needed — `block.input` is already a dict.
- `src/app/tests/fixtures/sample_invoices/` exists but is empty. Tests in this phase mock HTTP calls rather than hitting real APIs, so no real PDFs are needed.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Implement src/app/services/parser_llamaparse.py

**Verifies:** qclerq-invoice-pipeline.AC2.7 (partial — LlamaParse raise on non-200 tested here)

**Files:**
- Modify: `src/app/services/parser_llamaparse.py` (currently empty stub)

**Implementation:**

LlamaParse v2 HTTP API:
- Base URL: `https://api.cloud.llamaindex.ai`
- Auth: `Authorization: Bearer <key>`
- Flow: POST `/api/v2/parse/upload` (multipart) → POST `/api/v2/parse` (JSON) → GET `/api/v2/parse/{job_id}?expand=text_full` (poll until COMPLETED)

```python
from __future__ import annotations

import time

import httpx

_BASE_URL = "https://api.cloud.llamaindex.ai"
_POLL_INTERVAL_S = 2
_MAX_POLLS = 60


def parse_pdf(pdf_bytes: bytes, filename: str, api_key: str) -> str:
    """Upload PDF to LlamaParse v2 and return extracted plain text.

    Raises RuntimeError on non-200 responses or job failure, so callers can
    fall back to parser_pdfco.
    """
    headers = {"Authorization": f"Bearer {api_key}"}

    with httpx.Client(base_url=_BASE_URL, headers=headers, timeout=30) as client:
        upload_resp = client.post(
            "/api/v2/parse/upload",
            files={"file": (filename, pdf_bytes, "application/pdf")},
            data={"configuration": '{"tier": "agentic", "version": "latest"}'},
        )
        if upload_resp.status_code != 200:
            raise RuntimeError(
                f"llamaparse upload failed: {upload_resp.status_code} {upload_resp.text}"
            )
        file_id = upload_resp.json()["file_id"]

        parse_resp = client.post(
            "/api/v2/parse",
            json={"file_id": file_id, "tier": "agentic", "version": "latest"},
        )
        if parse_resp.status_code != 200:
            raise RuntimeError(
                f"llamaparse parse job failed: {parse_resp.status_code} {parse_resp.text}"
            )
        job_id = parse_resp.json()["job"]["id"]

        for _ in range(_MAX_POLLS):
            time.sleep(_POLL_INTERVAL_S)
            result_resp = client.get(f"/api/v2/parse/{job_id}?expand=text_full")
            if result_resp.status_code != 200:
                raise RuntimeError(
                    f"llamaparse poll failed: {result_resp.status_code} {result_resp.text}"
                )
            data = result_resp.json()
            status = data.get("job", {}).get("status", "")
            if status == "COMPLETED":
                return data.get("text_full", "") or ""
            if status in ("FAILED", "ERROR"):
                raise RuntimeError(f"llamaparse job failed with status: {status}")

        raise RuntimeError("llamaparse job timed out after polling")
```

**Step 1: Write the implementation above to `src/app/services/parser_llamaparse.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.parser_llamaparse import parse_pdf; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/parser_llamaparse.py
git commit -m "feat: implement LlamaParse v2 PDF parser"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement src/app/services/parser_pdfco.py

**Verifies:** qclerq-invoice-pipeline.AC2.7 (partial — PDF.co fallback invoked when LlamaParse raises)

**Files:**
- Modify: `src/app/services/parser_pdfco.py` (currently empty stub)

**Implementation:**

PDF.co API:
- Endpoint: `POST https://api.pdf.co/v1/pdf/convert/to/text`
- Auth: `x-api-key` header
- Request body: `{"url": "...", "inline": true, "async": false}`

Since n8n passes raw PDF bytes (not a URL), we need to base64-encode them and use PDF.co's `/api/pdf/convert/to/text` endpoint with the `file` (base64) parameter, OR upload to a temp URL first. PDF.co supports inline base64 via `fileUrl` pointing to a data URI, OR via `file` base64 field. Use the `file` + `name` pattern:

```python
from __future__ import annotations

import base64

import requests

_ENDPOINT = "https://api.pdf.co/v1/pdf/convert/to/text"


def parse_pdf(pdf_bytes: bytes, filename: str, api_key: str) -> str:
    """Extract text from PDF bytes via PDF.co API.

    Raises RuntimeError on non-200 responses or API error.
    """
    encoded = base64.b64encode(pdf_bytes).decode()

    payload = {
        "file": encoded,
        "name": filename,
        "inline": True,
        "async": False,
    }
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    resp = requests.post(_ENDPOINT, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(
            f"pdfco request failed: {resp.status_code} {resp.text}"
        )
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"pdfco api error: {data.get('message', 'unknown')}")
    return data.get("body", "") or ""
```

**Step 1: Write the implementation above to `src/app/services/parser_pdfco.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.parser_pdfco import parse_pdf; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/parser_pdfco.py
git commit -m "feat: implement PDF.co fallback PDF parser"
```
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-4) -->

<!-- START_TASK_3 -->
### Task 3: Write src/app/prompts/invoice_extraction.md

**Verifies:** None (prompt content — tested indirectly via extractor tests)

**Files:**
- Modify: `src/app/prompts/invoice_extraction.md` (currently empty stub)

**Implementation:**

```markdown
You are an invoice data extraction specialist. Extract all structured fields from the invoice text provided.

## Field Definitions

- `invoice_number`: The invoice identifier (e.g., "INV-1234", "2024-001"). Null if not found.
- `invoice_date`: The date the invoice was issued. ISO format YYYY-MM-DD. Null if not found.
- `due_date`: Payment due date. ISO format YYYY-MM-DD. Null if not found.
- `vendor_raw`: The vendor/supplier name exactly as it appears on the invoice. Never null.
- `vendor_normalized`: Same as vendor_raw for now — the caller will normalize it. Copy vendor_raw here.
- `po_number`: Purchase order number if present. Null if not found.
- `job_id`: Job or project reference number if present. Null if not found.
- `subtotal`: Pre-tax subtotal amount as a number (no currency symbols). Use 0.0 if not stated.
- `tax`: Tax amount as a number. Use 0.0 if not stated.
- `shipping`: Shipping/freight amount as a number. Use 0.0 if not stated.
- `discount`: Discount amount as a positive number (will be subtracted). Use 0.0 if not stated.
- `total`: The final total amount due. Required — estimate from line items if not stated explicitly.
- `currency`: Three-letter ISO currency code (e.g., "USD", "CAD"). Default "USD" if not specified.
- `line_items`: Array of line items. Each has: description, quantity, unit_price, line_total, category.
- `payment_terms`: Payment terms (e.g., "Net 30", "Due on receipt"). Null if not found.
- `confidence_overall`: Float 0.0–1.0. How reliably did you read this invoice?
  - 0.9–1.0: Clean digital PDF, all fields clearly visible
  - 0.7–0.89: Some fields unclear or ambiguous but main data readable
  - 0.5–0.69: Scanned/photographed, significant reconstruction needed
  - Below 0.5: Very poor quality, many fields guessed
- `duplicate_risk`: "none" (no reason to suspect duplicate), "possible" (some fields match known patterns), "likely" (looks identical to a prior invoice). Default "none" unless you see explicit signals.
- `missing_required_fields`: List field names that are absent or unreadable. Required fields: invoice_number, vendor_raw, total, invoice_date.
- `warnings`: List any anomalies (e.g., "line items don't sum to subtotal", "date appears to be in the past by >1 year").
- `file_hash`: Leave as empty string "" — the caller fills this in.
- `file_name`: Leave as empty string "" — the caller fills this in.

## Line Item Categories

Classify each line item into one of: materials, labor, software, utilities, rent, unknown.

## Normalization Rules

- Remove currency symbols from amounts.
- Dates: convert to YYYY-MM-DD. If only month/year given, use the 1st of the month.
- If subtotal is not stated but line items are given, sum them for subtotal.
- If total is not stated, estimate as subtotal + tax + shipping - discount.

## Important

Extract only what is present. Do not invent values. Use null for missing optional fields.
Set confidence_overall honestly — low confidence is useful signal, not a failure.
```

**Step 1: Write the prompt content above to `src/app/prompts/invoice_extraction.md`**

**Step 2: Verify file is readable**

```bash
python -c "import pathlib; p = pathlib.Path('src/app/prompts/invoice_extraction.md'); print(len(p.read_text()), 'chars')"
```

Expected: prints a char count > 0

**Step 3: Commit**

```bash
git add src/app/prompts/invoice_extraction.md
git commit -m "feat: add invoice extraction prompt with field definitions and normalization rules"
```
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Implement src/app/services/extractor_claude.py

**Verifies:** qclerq-invoice-pipeline.AC2.1, qclerq-invoice-pipeline.AC2.2

**Files:**
- Modify: `src/app/services/extractor_claude.py` (currently empty stub)

**Implementation:**

Uses Claude `tool_use` with `tool_choice` forced, so the response is always a structured dict. The tool schema mirrors `InvoiceExtracted`. On any failure, returns `None` (never raises).

```python
from __future__ import annotations

import json
import pathlib
from datetime import date
from typing import Any

import anthropic

from src.app.schemas.invoice import ExceptionItem, InvoiceExtracted, LineItem

_PROMPT_PATH = pathlib.Path(__file__).parent.parent / "prompts" / "invoice_extraction.md"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096

_TOOL_SCHEMA: dict[str, Any] = {
    "name": "extract_invoice",
    "description": "Extract all structured fields from an invoice document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "invoice_number":          {"type": ["string", "null"]},
            "invoice_date":            {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "due_date":                {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "vendor_raw":              {"type": "string"},
            "vendor_normalized":       {"type": "string"},
            "po_number":               {"type": ["string", "null"]},
            "job_id":                  {"type": ["string", "null"]},
            "subtotal":                {"type": "number"},
            "tax":                     {"type": "number"},
            "shipping":                {"type": "number"},
            "discount":                {"type": "number"},
            "total":                   {"type": "number"},
            "currency":                {"type": "string"},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "quantity":    {"type": "number"},
                        "unit_price":  {"type": "number"},
                        "line_total":  {"type": "number"},
                        "category":    {"type": "string"},
                    },
                    "required": ["description", "quantity", "unit_price", "line_total", "category"],
                },
            },
            "payment_terms":           {"type": ["string", "null"]},
            "confidence_overall":      {"type": "number"},
            "duplicate_risk":          {"type": "string", "enum": ["none", "possible", "likely"]},
            "missing_required_fields": {"type": "array", "items": {"type": "string"}},
            "warnings":                {"type": "array", "items": {"type": "string"}},
            "file_hash":               {"type": "string"},
            "file_name":               {"type": "string"},
        },
        "required": [
            "vendor_raw", "vendor_normalized", "subtotal", "tax", "shipping",
            "discount", "total", "currency", "line_items", "confidence_overall",
            "duplicate_risk", "missing_required_fields", "warnings", "file_hash", "file_name",
        ],
    },
}


def extract(
    raw_text: str,
    file_hash: str,
    file_name: str,
    api_key: str,
) -> InvoiceExtracted | None:
    """Extract invoice fields from raw text using Claude tool_use.

    Returns None on any failure (caller handles fallback or exception routing).
    Never raises.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "extract_invoice"},
            messages=[{"role": "user", "content": f"Extract all invoice fields:\n\n{raw_text}"}],
        )
    except Exception:
        return None

    if response.stop_reason != "tool_use":
        return None

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        return None

    data: dict[str, Any] = tool_block.input
    data["file_hash"] = file_hash
    data["file_name"] = file_name

    try:
        return InvoiceExtracted.model_validate(data)
    except Exception:
        return None
```

**Step 1: Write the implementation above to `src/app/services/extractor_claude.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.extractor_claude import extract; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/extractor_claude.py
git commit -m "feat: implement Claude extraction service using tool_use"
```
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 5-6) -->

<!-- START_TASK_5 -->
### Task 5: Write a combined parse_and_extract helper in src/app/services/extractor_claude.py

**Verifies:** qclerq-invoice-pipeline.AC2.7 (LlamaParse → PDF.co fallback wiring)

**Files:**
- Modify: `src/app/services/extractor_claude.py` (add function at the bottom)

**Implementation:**

Add this function after the `extract` function in `extractor_claude.py`:

```python
def parse_and_extract(
    pdf_bytes: bytes,
    file_hash: str,
    file_name: str,
    llama_api_key: str,
    pdfco_api_key: str,
    anthropic_api_key: str,
) -> InvoiceExtracted | None:
    """Parse PDF and extract fields. Falls back to PDF.co if LlamaParse fails.

    Returns None if both parsers fail or extraction fails.
    """
    from src.app.services import parser_llamaparse, parser_pdfco

    raw_text: str | None = None

    try:
        raw_text = parser_llamaparse.parse_pdf(pdf_bytes, file_name, llama_api_key)
    except Exception:
        pass

    if not raw_text:
        try:
            raw_text = parser_pdfco.parse_pdf(pdf_bytes, file_name, pdfco_api_key)
        except Exception:
            return None

    if not raw_text:
        return None

    return extract(raw_text, file_hash, file_name, anthropic_api_key)
```

**Step 1: Add the `parse_and_extract` function to the bottom of `src/app/services/extractor_claude.py`**

**Step 2: Verify importable**

```bash
python -c "from src.app.services.extractor_claude import parse_and_extract; print('ok')"
```

Expected: prints `ok`

**Step 3: Commit**

```bash
git add src/app/services/extractor_claude.py
git commit -m "feat: add parse_and_extract with LlamaParse→PDF.co fallback"
```
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Implement src/app/tests/test_extraction_goldens.py

**Verifies:** qclerq-invoice-pipeline.AC2.1, qclerq-invoice-pipeline.AC2.2, qclerq-invoice-pipeline.AC2.7

**Files:**
- Modify: `src/app/tests/test_extraction_goldens.py` (currently empty stub)

**What to test (mock HTTP + Claude calls):**
- `AC2.1`: `extract()` called with mocked Claude response returns `InvoiceExtracted` with all required fields set
- `AC2.2`: `extract()` with a "clean" invoice prompt → `confidence_overall >= 0.7`; with low-quality signal → `confidence_overall < 0.7`
- `AC2.7`: `parse_and_extract()` — when `parser_llamaparse.parse_pdf` raises, `parser_pdfco.parse_pdf` is called instead
- `extract()` returns `None` when Claude returns stop_reason != "tool_use"
- `extract()` returns `None` when `InvoiceExtracted.model_validate()` fails (bad data from Claude)

Tests use `unittest.mock.patch` to mock `httpx.Client`, `requests.post`, and `anthropic.Anthropic`. No real API keys required.

**Step 1: Implement test_extraction_goldens.py**

The test file already exists as an empty stub — implement it. Mock the Claude `messages.create` response by constructing a fake `anthropic.types.Message`-like object using `MagicMock`.

Key mock pattern for Claude tool_use response:
```python
from unittest.mock import MagicMock, patch

def make_claude_tool_response(data: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.input = data
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response
```

**Step 2: Run tests**

```bash
pytest src/app/tests/test_extraction_goldens.py -v
```

Expected: All tests pass.

**Step 3: Commit**

```bash
git add src/app/tests/test_extraction_goldens.py
git commit -m "test: add extraction golden tests with mocked Claude and parser calls"
```
<!-- END_TASK_6 -->

<!-- END_SUBCOMPONENT_C -->

---

## Phase 2 Done When

- `python -c "from src.app.services.extractor_claude import parse_and_extract"` exits 0
- `pytest src/app/tests/test_extraction_goldens.py -v` passes
- `parse_and_extract` calls `parser_pdfco.parse_pdf` when `parser_llamaparse.parse_pdf` raises (verified by AC2.7 test)
