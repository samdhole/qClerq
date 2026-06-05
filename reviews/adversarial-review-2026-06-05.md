# Adversarial Code & Design Review

**Date:** 2026-06-05
**Scope:** Current checkout on `master`: `src/app/api.py`, schemas, validator, sync services, tests, and `workflows/n8n_invoice_desk.json`
**Reviewer:** Codex (adversarial pass)
**Supersedes:** `reviews/adversarial-review-2026-06-04.md`, `reviews/adversarial-review-2026-05-13.md`

---

## Current Status

The original 2026-06-04 critical workflow bug is fixed in the current checkout: the n8n graph no longer chains `Call /approval-callback` into `Call /sync API`; auto invoices route to `/sync`, while manager/CFO invoices route through the approval email and then `/approval-callback`.

Several older findings are also fixed in the Python layer: `/sync` recomputes approval tier server-side, `/approval-callback` fails closed on an empty human approver allowlist, config is validated at startup, and the workflow uses an n8n header credential instead of inline backend API key literals.

The remaining problems are boundary failures. The backend now distrusts client-supplied tier, but still trusts the orchestrator to have already validated the invoice and to have used the correct tier-specific approval identity.

---

## Summary

| Severity | Count |
|----------|-------|
| HIGH | 4 |
| MEDIUM | 1 |
| **Total** | **5** |

Verification run: `uv run pytest src/app/tests` passed with **144 passed**.

---

## HIGH

### H-1 - Write endpoints bypass validation entirely

**Files:** `src/app/api.py:257-303`, `src/app/schemas/invoice.py:70-75`, `src/app/services/validator.py:78-126`

`/sync` and `/approval-callback` recompute the approval tier and check the approver shape, but neither endpoint re-runs deterministic validation or requires a server-issued validation token/result. Both accept a full `SyncRequest` containing an `InvoiceExtracted` object and then call `_run_sync()` directly.

This means a caller with the backend API key can skip `/validate` and push an invalid invoice into Sheets, QuickBooks, and Jobber as long as the amount routes to the endpoint's expected tier. The n8n graph happens to call `/validate` first, but the money-moving endpoints do not enforce that contract.

I reproduced this locally with `TestClient` and mocked external writes:

- Auto-tier invoice with `subtotal=1.00`, `tax=0`, `total=100.00` posted directly to `/sync` returned `200`.
- Manager-tier invoice with `subtotal=1.00`, `tax=0`, `total=1000.00` posted directly to `/approval-callback` returned `200`.
- In both cases, mocked Sheets, QuickBooks, and Jobber write paths were called.

**Fix:** make `_run_sync()` unreachable until the server has validated the invoice in the same request path. Options: run `validator.validate()` inside both write endpoints and reject `not is_clean`; or issue a short-lived validation artifact from `/validate` keyed to `file_hash` and require it on sync.

---

### H-2 - CFO approval is email-routed, not server-enforced

**Files:** `src/app/api.py:164-182`, `src/app/api.py:257-272`, `src/app/config.py:27-28`, `src/app/config.py:51`, `workflows/n8n_invoice_desk.json:768-818`

The workflow has a distinct CFO approval email node, but the backend only checks `approved_by` against one global `valid_approvers` list. It does not enforce that a CFO-tier invoice must be approved by `settings.cfo_email` or a CFO-specific allowlist.

I reproduced a `$10,000` invoice posted to `/approval-callback` with `approved_by="manager@example.com"` while both manager and CFO addresses were in `valid_approvers`; the endpoint returned `200` and invoked the mocked write paths. The server recomputed `approval_tier="cfo"` for audit, but still accepted the manager identity.

**Fix:** make approver policy tier-aware. For example: auto uses the `auto-approved` sentinel, manager requires `manager_email` or `valid_manager_approvers`, and CFO requires `cfo_email` or `valid_cfo_approvers`.

---

### H-3 - External idempotency fails open

**Files:** `src/app/services/quickbooks_sync.py:74-82`, `src/app/services/jobber_sync.py:107-116`, `src/app/tests/test_quickbooks_sync.py:207-248`, `src/app/tests/test_jobber_sync.py:139-171`

QuickBooks and Jobber both attempt idempotency checks before create, but both checks are explicitly best-effort. If the lookup/search fails, the code proceeds to create the external record anyway.

That behavior is also codified in tests:

- QuickBooks: `test_idempotency_query_failure_does_not_block_create`
- Jobber: `test_idempotency_query_failure_does_not_block_create`

For a financial pipeline, "cannot prove this invoice was not already posted" should not default to "create another Bill/expense." This is especially risky because `docs/DEPLOY_STATUS_2026-06-04.md` states runtime idempotency was not live-verified, and the Jobber search query was previously called out as schema-uncertain.

**Fix:** fail closed on idempotency lookup failure for external writes, or write a pending/error state that requires human retry after confirming no duplicate exists. If availability is more important than strict blocking, add an explicit operator override path rather than automatic create.

---

### H-4 - Invoice amount fields are unconstrained and allow negative/non-finite values

**Files:** `src/app/schemas/invoice.py:9-14`, `src/app/schemas/invoice.py:17-38`, `src/app/services/validator.py:30-126`, `src/app/services/approval_router.py:6-18`

The Pydantic models use bare `float` for invoice totals, subtotals, taxes, quantities, unit prices, and line totals. The validator checks arithmetic consistency, but does not require values to be finite or non-negative.

I verified a negative invoice with `subtotal=-100.00` and `total=-100.00` validates clean and routes `auto`, because the arithmetic is internally consistent and `-100 < tier_1_max`.

The same schema also accepts `float("nan")` when constructed in Python. Even if JSON transport limits some non-finite payloads, internal test/build code and LLM result conversion should not be able to produce non-finite financial fields.

**Fix:** use constrained numeric fields or validators: finite numbers only; positive `total`; non-negative subtotal/tax/shipping/discount/line totals; positive quantity where line items exist. Decide separately how credit memos should be represented rather than letting negative invoices masquerade as normal invoices.

---

## MEDIUM

### M-1 - Exceptions are appended by both backend and n8n

**Files:** `src/app/api.py:148-160`, `src/app/services/sheets_sync.py:122-140`, `workflows/n8n_invoice_desk.json:208-323`, `workflows/n8n_invoice_desk.json:914-929`

When `/validate` returns `is_clean=false`, the backend appends exception rows to the Exceptions sheet. The workflow then sees the same invalid validation result, routes the true branch of `Route Exceptions`, and appends another exception row through the n8n `Write to Exceptions Sheet` node before notifying the exception reviewer.

This creates duplicate queue entries for the same invalid invoice. The weekly report now counts distinct `file_hash`, so the rate is protected, but the actual review queue and notification context are still polluted.

**Fix:** choose one writer. Prefer making `/validate` pure and letting n8n own exception queue side effects, or remove the n8n write node and let the backend return an exception-write receipt for notification.

---

## Fixed Older Findings

These were current in older reviews but did not reproduce in the present checkout:

- The original approval double-fire edge is gone: `Check Approval Decision` now routes to `Call /approval-callback`, not to `/sync`.
- `/sync` rejects high-value invoices spoofed as `approval_tier="auto"` by recomputing tier from `invoice.total`.
- Human approvals fail closed when `valid_approvers` is empty.
- Manager and CFO approval emails are now separate workflow nodes.
- Exception notifications exist in the workflow.
- Config is validated during app startup.
- Backend API key literals are not present in the current workflow export; backend HTTP nodes reference an n8n header credential.

The older review files are retained for history but are marked stale because several listed defects are no longer current.

---

## Recommended Fix Order

1. Enforce deterministic validation inside `/sync` and `/approval-callback`.
2. Split approver policy by tier, especially CFO.
3. Change external idempotency failure from fail-open to fail-closed.
4. Add finite/non-negative financial field validation.
5. Remove one of the duplicate exception writers.
