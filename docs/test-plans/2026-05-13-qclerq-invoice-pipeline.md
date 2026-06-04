# Human Test Plan — qClerq AI Invoice Pipeline
# Generated: 2026-05-13 | Updated: 2026-06-04 | Automated coverage: 26/26 ACs | Tests: 112 passed | Manual: P1.1 P1.2 P1.3 P1.4 P2.1-2.5 P3.1 P5.1 P5.2 P5.3 P6.3 ✅

## Prerequisites

- `.env` populated with: `API_KEY`, `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `MANAGER_EMAIL`, `CFO_EMAIL`, `VALID_APPROVERS` (QB/Jobber optional)
- FastAPI app running: `uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 9100` (ports 8000/8001 reserved by Windows — use 9100)
- n8n running locally with `workflows/n8n_invoice_desk.json` imported and credentials re-attached:
  - Google OAuth2 → `Invoice Folder Monitor`, `Download Invoice PDF`
  - Gmail OAuth2 → `Monitor Gmail Invoices`, `Send Invoice for Approval`, `Send Rejection Notification`
  - Google Sheets OAuth2 → `Write to Exceptions Sheet`
  - n8n env var: `INVOICE_DRIVE_FOLDER_ID=<your Drive folder ID>`
- `uv run pytest src/app/tests/ -v` passes (114 passed)
- Google Sheet `1Vy7dvq18Jh6CSoNBkNk1YMjN9btXGIsLv5nQdO7i9sY` has `Invoices` and `Exceptions` tabs with header rows matching `src/app/services/sheets_sync.py` (INVOICE_COLUMNS / EXCEPTION_COLUMNS)
- Sheet shared with service account `qclerq@gen-lang-client-0832688008.iam.gserviceaccount.com` (Editor)

## Status as of 2026-06-04

| Phase | Status | Notes |
|-------|--------|-------|
| Prerequisites | ✅ Done | `.env` set, backend on 9100, 112 tests passing, Sheets headers written |
| Phase 1 — Intake triggers | ✅ Done | 1.1 ✅ Gmail trigger (exec #106, MES-2026-0089 $7,500, CFO email); 1.2 ✅ Drive trigger (exec #113, MES-2026-0089 $7,500 → full pipeline → CFO email sent); 1.3 ✅ Web form (exec #78); 1.4 ✅ guard rails (415/413/401) |
| Phase 2 — Approval routing | ✅ Done | 2.1 ✅ auto (exec #78); 2.2 ✅ manager email (exec #90); 2.3 ✅ CFO email (exec #91, MES-2026-0089 $7,500); 2.4 ✅ approved_by/notes/at on Sheets row 5; 2.5 ✅ rejection email sent, no sync |
| Phase 3 — Proof trail | ⚠️ Partial | Sheets row 5: approved_by, approved_at, file_hash all present; QB/Jobber IDs null (no creds) |
| Phase 4 — Failure isolation | ❌ Pending | Requires QB/Jobber creds |
| Phase 5 — Duplicates/low-conf | ✅ Done | 5.1 ✅ duplicate detected (exec #92, POS-2026-0201 hash match → Exceptions row 14); 5.2 ✅ low-conf confirmed (Invoice-LOW-CONFIDENCE-scan.pdf, confidence=0.40, 4 exceptions); 5.3 ✅ math error (IKEA) |
| Phase 6 — Weekly report | ✅ Done | `/report/weekly` returns 200 with live data |
| E2E happy path | ✅ Done | Extract → validate → approval-callback → Sheets row confirmed |

---

## Phase 1: Intake Trigger Verification

| Step | Action | Expected |
|------|--------|----------|
| 1.1 | Send email to monitored Gmail inbox with valid PDF attached (AC1.1) | n8n `Monitor Gmail Invoices` fires; `POST /extract` returns 200 |
| 1.2 | Drop PDF into monitored Drive folder (AC1.2) | `Invoice Folder Monitor` triggers; `Download Invoice PDF` succeeds; `/extract` called |
| 1.3 ✅ | Submit Web Upload Form with valid PDF (AC1.3) — confirmed 2026-05-14 exec #78: Invoice-9723.pdf (Minerva Networks, $23.40 EUR) | Form submits; n8n routes to `/extract`; 200 returned; all 7 nodes green |
| 1.4 | Send `.docx` or `.png` via monitored Gmail (AC1.4) | n8n filter drops before `/extract`. Force-test: `curl -X POST http://127.0.0.1:9100/extract -H "X-API-Key: <key>" -F "file=@something.docx"` returns 415 |

---

## Phase 2: Approval Tier Routing

| Step | Action | Expected |
|------|--------|----------|
| 2.1 ✅ | Upload PDF with total < $500 via Web Form — confirmed 2026-05-14 exec #78 (Invoice-9723.pdf, $23.40 EUR) | `/validate` returns `approval_tier="auto"` (AC3.1); no approval email; `/sync` runs immediately; Sheets row created |
| 2.2 ✅ | Upload PDF with total $500–$5000 — confirmed 2026-05-14 exec #90: Invoice-POS-2026-0201-manager.pdf (Pinnacle Office Supplies, $2,150 USD) | `approval_tier="manager"` (AC3.2); approval email arrives with Approve/Reject links |
| 2.3 ✅ | Upload PDF with total > $5000 — confirmed 2026-05-14 exec #91: Invoice-MES-2026-0089-cfo.pdf (Meridian Enterprise Solutions, $7,500 USD) | `approval_tier="cfo"` (AC3.3); CFO approval email arrived |
| 2.4 ✅ | Click "Approve" in the manager email — confirmed 2026-05-14 exec #90, Sheets row 5 | Sheets row gains `approved_by=enigman.kk@gmail.com`, `approval_notes`, `approved_at` (ISO timestamp); success page shown |
| 2.5 ✅ | Click "Reject" on CFO invoice (exec #91) — confirmed 2026-05-14 | Rejection email `[Rejected] Invoice from Meridian Enterprise Solutions Inc.` sent; no `Call /approval-callback` or sync executed |

---

## Phase 3: Proof Trail Audit on Sheets Row (AC6)

Pick an approved invoice from step 2.1.

| Step | Action | Expected |
|------|--------|----------|
| 3.1 | Open the Sheets `Invoices` tab row | Columns populated: `approval_tier`, `approved_by`, `approved_at` (AC6.1) |
| 3.2 | Inspect `sync_status` column | JSON dict with keys `sheets`, `quickbooks`, `jobber` each `"ok"` or `"failed"` (AC4.6) |
| 3.3 | Inspect ID columns | `qb_bill_id` and `jobber_expense_id` populated for successful targets (AC6.1) — requires QB/Jobber creds |
| 3.4 | Inspect `file_hash` column | 64-char SHA-256 hex string present (AC6.3) |

---

## Phase 4: Failure Isolation in Sync

| Step | Action | Expected |
|------|--------|----------|
| 4.1 | Revoke QB token; upload valid invoice | Sheets row written; Jobber expense created; `sync_status["quickbooks"]="failed"`, others `"ok"` (AC4.4) |
| 4.2 | Restore QB; revoke Jobber token; upload another invoice | Sheets + QB succeed; `sync_status["jobber"]="failed"` (AC4.5) |
| 4.3 | Restore all tokens | Subsequent uploads: all three statuses `"ok"` |

---

## Phase 5: Duplicate and Low-Confidence Handling

| Step | Action | Expected |
|------|--------|----------|
| 5.1 ✅ | Upload the same PDF from step 2.1 a second time (AC1.5) — confirmed 2026-05-14 exec #92: POS-2026-0201 re-uploaded | `Exceptions` tab row 14: `issue_type="duplicate_risk"`, `duplicate_risk="likely"`, `status="open"` |
| 5.2 | Upload handwritten / low-res scan (AC2.6) | `Exceptions` row with `issue_type="low_confidence"`, severity set, `status="open"` |
| 5.3 ✅ | Upload PDF where subtotal+tax ≠ total — tested with IKEA CAINV26000001413522 (2026-05-14) | `Exceptions` row with `issue_type="math_error"` confirmed; 6 exceptions flagged (AC2.4) |

---

## Phase 6: Weekly Report

| Step | Action | Expected |
|------|--------|----------|
| 6.1 | In n8n, manually execute `Weekly Report Trigger` workflow (AC7.2) | `/report/weekly` called; `Send Weekly Report Email` node green |
| 6.2 | Check `enigman.kk@gmail.com` inbox | Email arrives with `WeeklySummary`: invoice count, total value, top vendors, sync failures, period dates |
| 6.3 ✅ | `curl http://127.0.0.1:9100/report/weekly -H "X-API-Key: <key>"` | Returns 200 with valid `WeeklySummary` — confirmed 2026-05-14: 1 invoice, $837.04, top vendor IKEA Canada (AC7.1, AC7.3) |

---

## End-to-End: 60-second Auto-Approve Path

**Purpose:** Validates the full happy path — PDF → Sheets-with-proof-trail in ~60s.

1. Start timer.
2. Submit a small-total (<$500) PDF via the Web Upload Form.
3. Watch n8n execution view: each node turns green sequentially (Trigger → /extract → /validate → /sync).
4. Open Sheets `Invoices` tab; confirm the new row appears.
5. Stop timer.

**Expected:** elapsed ≤ ~60s; row contains all required fields, proof trail columns, file_hash, and `sync_status` showing all three targets `"ok"`.

**Direct-API variant (no n8n) ✅ confirmed 2026-05-14:**
```
POST /extract   → 200, confidence 0.95, IKEA invoice
POST /approval-callback → 200, sheets_row_id: 2
GET  /report/weekly     → 200, invoice_count: 1, total_value: 837.04
```

---

## Traceability

| AC | Automated Test | Manual Step |
|----|----------------|-------------|
| AC1.1 | — | Phase 1.1 |
| AC1.2 | — | Phase 1.2 |
| AC1.3 | — | Phase 1.3 |
| AC1.4 | — | Phase 1.4 |
| AC1.5 | `test_dedupe.py::test_known_hash_is_likely_duplicate` | Phase 5.1 |
| AC2.1 | `test_extraction_goldens.py::test_ac21_valid_text_returns_invoice_extracted` | E2E step 3 |
| AC2.2 | `test_extraction_goldens.py::test_ac22_*` | Phase 5.2 |
| AC2.3 | `test_validation.py::test_math_check_passes_when_balanced` | E2E |
| AC2.4 | `test_validation.py::test_math_error_when_totals_mismatch` | Phase 5.3 ✅ |
| AC2.5 | `test_validation.py::test_missing_*` | — |
| AC2.6 | `test_validation.py::test_low_confidence_below_floor` | Phase 5.2 |
| AC2.7 | `test_extraction_goldens.py::test_ac27_llamaparse_fails_fallback_to_pdfco` | — |
| AC3.1 | `test_api.py::test_validate_auto_tier` | Phase 2.1 |
| AC3.2 | `test_api.py::test_validate_manager_tier` | Phase 2.2 |
| AC3.3 | `test_api.py::test_validate_cfo_tier` | Phase 2.3 |
| AC3.4 | — | Phase 2.4 |
| AC3.5 | `test_api.py::test_approval_callback_rejects_missing_approved_by` | — |
| AC4.1 | `test_sync.py::test_sync_all_sheets_success` | E2E step 4 ✅ |
| AC4.2 | `test_quickbooks_sync.py::test_create_bill_uses_vendor_and_line_items` | Phase 3.3 |
| AC4.3 | `test_jobber_sync.py::test_jobber_expense_payload_contains_total_and_job_id` | Phase 3.3 |
| AC4.4 | `test_sync.py::test_sync_all_qb_failure_does_not_block_jobber` | Phase 4.1 |
| AC4.5 | `test_sync.py::test_sync_all_jobber_failure_does_not_block_sheets` | Phase 4.2 |
| AC4.6 | `test_sync.py::test_sync_all_backfill_sync_status` | Phase 3.2 |
| AC5.1 | `test_extraction_goldens.py::test_ac51_exact_match_*` | — |
| AC5.2 | `test_extraction_goldens.py::test_ac52_fuzzy_match_*` | — |
| AC5.3 | `test_extraction_goldens.py::test_ac53_novel_vendor_llm_success` | — |
| AC5.4 | `test_extraction_goldens.py::test_ac54_*` | — |
| AC6.1 | `test_sync.py::test_sync_all_sheets_row_has_proof_trail_columns` | Phase 3.1, 3.3 |
| AC6.2 | `test_sheets_sync.py::test_write_exceptions_row_structure` | Phase 5.1 |
| AC6.3 | `test_sheets_sync.py::test_write_invoice_row_includes_file_hash` | Phase 3.4 |
| AC7.1 | `test_report.py::test_get_report_weekly_endpoint_returns_valid_summary` | Phase 6.3 ✅ |
| AC7.2 | — | Phase 6.1–6.2 |
| AC7.3 | `test_report.py::test_get_report_weekly_endpoint_with_empty_week` | Phase 6.3 ✅ |
