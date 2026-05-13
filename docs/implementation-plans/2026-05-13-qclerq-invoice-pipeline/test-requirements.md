# Test Requirements — qClerq Invoice Pipeline

Source: `docs/implementation-plans/2026-05-13-qclerq-invoice-pipeline/phase_01.md` … `phase_08.md`
Design: `docs/design-plans/2026-05-13-qclerq-invoice-pipeline.md`
Generated: 2026-05-13

This document maps every Acceptance Criterion (AC1.1–AC7.3) to its automated test (file + test function) or its manual / operational verification path. Automation coverage is required for every criterion marked **Automated**; criteria marked **Manual** are verified at runtime against n8n / Sheets / Gmail / QuickBooks / Jobber and have no pytest equivalent.

---

## Automated Test Coverage Required

These ACs must be covered by an executable pytest test. Each row names the file the test lives in and the specific test function that verifies the behavior.

| AC | Description | Test File | Test Function | Phase |
|----|-------------|-----------|---------------|-------|
| AC1.5 | Duplicate PDF (same hash) detected before extraction | `src/app/tests/test_dedupe.py` | `test_check_duplicate_likely_when_hash_present`, `test_check_duplicate_none_when_hash_absent` | 4 |
| AC2.1 | Valid PDF returns `InvoiceExtracted` with all required fields populated | `src/app/tests/test_extraction_goldens.py` | `test_extract_returns_invoice_extracted_with_required_fields` | 2 |
| AC2.2 | `confidence_overall` reflects PDF readability | `src/app/tests/test_extraction_goldens.py` | `test_extract_confidence_high_for_clean_invoice`, `test_extract_confidence_low_for_noisy_invoice` | 2 |
| AC2.3 | Math check passes when subtotal+tax+shipping-discount = total ±$0.02 | `src/app/tests/test_validation.py` | `test_math_check_passes_when_totals_balance` | 4 |
| AC2.4 | Math mismatch produces `ExceptionItem(type="math_error")` | `src/app/tests/test_validation.py` | `test_math_check_fails_with_math_error_exception`, `test_math_check_tolerance_edge` | 4 |
| AC2.5 | Missing required fields produce `ExceptionItem` list | `src/app/tests/test_validation.py` | `test_missing_required_field_single`, `test_missing_all_required_fields` | 4 |
| AC2.6 | `confidence_overall < 0.70` produces low_confidence exception | `src/app/tests/test_validation.py` | `test_low_confidence_below_threshold`, `test_confidence_at_threshold_no_exception` | 4 |
| AC2.7 | PDF.co fallback used when LlamaParse returns non-200 | `src/app/tests/test_extraction_goldens.py` | `test_parse_and_extract_falls_back_to_pdfco_when_llamaparse_raises` | 2 |
| AC3.1 | Total < $500 → `approval_tier = "auto"` | `src/app/tests/test_api.py` | `test_validate_tier_auto_for_small_total` | 5 |
| AC3.2 | Total $500–$5000 → `approval_tier = "manager"` | `src/app/tests/test_api.py` | `test_validate_tier_manager_for_mid_total` | 5 |
| AC3.3 | Total > $5000 → `approval_tier = "cfo"` | `src/app/tests/test_api.py` | `test_validate_tier_cfo_for_large_total` | 5 |
| AC3.5 | Sync requests require `approved_by` (no write without approval) | `src/app/tests/test_api.py` | `test_sync_rejects_request_without_approval_fields` | 5 |
| AC4.1 | Approved invoice creates Sheets Invoices row with all fields | `src/app/tests/test_sync.py` | `test_sheets_sync_writes_invoice_row_with_all_fields` | 6 |
| AC4.2 | Approved invoice creates QB Bill with correct VendorRef + Lines | `src/app/tests/test_sync.py` | `test_quickbooks_sync_creates_bill_with_vendor_and_lines` | 6 |
| AC4.3 | Approved invoice creates Jobber expense with job_id + total | `src/app/tests/test_sync.py` | `test_jobber_sync_creates_expense_with_job_id_and_total` | 6 |
| AC4.4 | QB sync failure sets `sync_status["quickbooks"]="failed"` without blocking Jobber | `src/app/tests/test_sync.py` | `test_qb_failure_does_not_block_jobber_sync` | 6 |
| AC4.5 | Jobber sync failure sets `sync_status["jobber"]="failed"` without blocking Sheets | `src/app/tests/test_sync.py` | `test_jobber_failure_does_not_block_sheets_sync` | 6 |
| AC4.6 | Final `sync_status` dict written back to Sheets row | `src/app/tests/test_sync.py` | `test_sync_status_dict_written_back_to_sheets_row` | 6 |
| AC5.1 | Known vendor resolves to canonical name without LLM call | `src/app/tests/test_extraction_goldens.py` | `test_vendor_normalize_exact_match`, `test_vendor_normalize_case_insensitive` | 3 |
| AC5.2 | Near-match vendor resolves via rapidfuzz ≥88 | `src/app/tests/test_extraction_goldens.py` | `test_vendor_normalize_fuzzy_match` | 3 |
| AC5.3 | Novel vendor resolved by LLM fallback and promoted to vendors.json | `src/app/tests/test_extraction_goldens.py` | `test_vendor_normalize_llm_fallback_promotes_to_registry` | 3 |
| AC5.4 | `vendor_normalized` never empty — falls back to `vendor_raw` | `src/app/tests/test_extraction_goldens.py` | `test_vendor_normalize_falls_back_to_raw_on_llm_failure`, `test_vendor_normalize_empty_input` | 3 |
| AC6.1 | Every approved Sheets row carries full proof trail columns | `src/app/tests/test_sync.py` | `test_sheets_row_contains_proof_trail_columns` | 6 |
| AC6.2 | Exception rows include issue_type, severity, message, status="open" | `src/app/tests/test_sync.py` | `test_exceptions_tab_row_has_required_fields` | 6 |
| AC6.3 | `file_hash` stored on every row | `src/app/tests/test_sync.py` | `test_sheets_row_stores_file_hash` | 6 |
| AC7.1 | `GET /report/weekly` returns full `WeeklySummary` | `src/app/tests/test_report.py` | `test_generate_weekly_summary_with_data` | 8 |
| AC7.3 | Empty week returns zeroed `WeeklySummary` without error | `src/app/tests/test_report.py` | `test_generate_weekly_summary_empty_returns_zeroed` | 8 |

Run all automated coverage:
```
pytest src/app/tests/ -v
```

---

## Human Verification Required

These ACs depend on n8n triggers, OAuth-authenticated third-party services, or live email delivery and cannot be exercised by pytest. They are validated operationally during Phase 7 / Phase 8 demo runs.

| AC | Description | Verification Path | Phase |
|----|-------------|-------------------|-------|
| AC1.1 | Gmail attachment PDF triggers workflow and reaches `/extract` | n8n Gmail Trigger → `/extract` log check | 7 |
| AC1.2 | Drive folder new file triggers workflow and reaches `/extract` | n8n Drive Trigger → `/extract` log check | 7 |
| AC1.3 | Web upload form submission reaches `/extract` | n8n Web Form → `/extract` log check | 7 |
| AC1.4 | Non-PDF attachment is ignored / skipped | Send `.docx` via Gmail; confirm n8n filter drops it (and `/extract` returns 415 if forced) | 7 |
| AC3.4 | Approval callback records `approved_by`, `approval_notes`, `approved_at` in Sheets row | Click approval email link; inspect Sheets row | 7 |
| AC7.2 | n8n weekly Cron fires and report email delivered to owner | Wait for scheduled run or trigger Schedule Trigger manually; check inbox | 8 |

---

## End-to-End Demo Checklist (Phase 8 Task 4)

Target: full PDF → Sheets-with-proof-trail in ~60 seconds. Execute in order; check each box.

**Prerequisites**
- [ ] `uvicorn src.app.main:app --reload --host 127.0.0.1 --port 8000` is running
- [ ] `workflows/n8n_invoice_desk.json` imported into n8n with credentials re-attached:
  - [ ] Google OAuth2 on `Invoice Folder Monitor`, `Download Invoice PDF`
  - [ ] Gmail OAuth2 on `Monitor Gmail Invoices`, `Send Invoice for Approval`, `Send Rejection Notification`, `Send Weekly Report Email`
  - [ ] Google Sheets OAuth2 on `Write to Invoices Sheet`, `Write to Exceptions Sheet`
- [ ] `pytest src/app/tests/ -v` passes locally

**Auto-approve path (total < $500)**
- [ ] Upload a small-total PDF via the Web Upload Form
- [ ] n8n execution view shows each node green in sequence
- [ ] `POST /extract` returns `InvoiceExtracted` with all required fields (AC2.1)
- [ ] `POST /validate` returns `approval_tier = "auto"` (AC3.1)
- [ ] No approval email is sent
- [ ] `POST /sync` runs; Sheets Invoices row appears

**Manager / CFO path**
- [ ] Upload a mid-total PDF ($500–$5000): tier is `manager` (AC3.2), approval email arrives
- [ ] Upload a high-total PDF (>$5000): tier is `cfo` (AC3.3), approval email arrives
- [ ] Click approve in email; Sheets row gains `approved_by`, `approval_notes`, `approved_at` (AC3.4)

**Proof trail audit on the Sheets row (AC6)**
- [ ] `approval_tier`, `approved_by`, `approved_at` populated (AC6.1)
- [ ] `sync_status` dict shows outcome for each of `sheets`, `quickbooks`, `jobber` (AC4.6)
- [ ] `qb_bill_id` and `jobber_expense_id` present for successful targets (AC6.1)
- [ ] `file_hash` populated (AC6.3)

**Intake variants**
- [ ] Gmail path: send PDF as attachment; row reaches `/extract` (AC1.1)
- [ ] Drive path: drop PDF in monitored folder; row reaches `/extract` (AC1.2)
- [ ] Non-PDF rejection: send `.docx` via Gmail; n8n drops it before `/extract` (AC1.4)

**Duplicate detection (AC1.5)**
- [ ] Upload the same PDF a second time
- [ ] Exceptions tab shows new row with `duplicate_risk = "likely"`

**Low-confidence handling (AC2.6)**
- [ ] Upload a low-resolution or handwritten PDF
- [ ] Exceptions tab gains a row with `issue_type = "low_confidence"`, `status = "open"`

**Weekly report (AC7.2)**
- [ ] Manually trigger n8n Schedule Trigger
- [ ] Report email arrives at `enigman.kk@gmail.com` with `WeeklySummary` content

---

## Traceability Summary

| AC Range | Automated | Manual | Total |
|----------|-----------|--------|-------|
| AC1.x    | 1 (AC1.5) | 4 (AC1.1–1.4) | 5 |
| AC2.x    | 6 (AC2.1–2.7 minus none) | 0 | 7 |
| AC3.x    | 4 (AC3.1–3.3, 3.5) | 1 (AC3.4) | 5 |
| AC4.x    | 6 (AC4.1–4.6) | 0 | 6 |
| AC5.x    | 4 (AC5.1–5.4) | 0 | 4 |
| AC6.x    | 3 (AC6.1–6.3) | 0 | 3 |
| AC7.x    | 2 (AC7.1, AC7.3) | 1 (AC7.2) | 3 |
| **Total** | **26** | **6** | **32** |
