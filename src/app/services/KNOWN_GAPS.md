# Known Gaps and Limitations

This document tracks explicitly acknowledged gaps and limitations in the qClerq invoice system.

## Gemini Model Pin

**Status:** Hard-coded constant

Extraction uses `gemini-3.1-flash-lite`, defined as `GEMINI_MODEL` in `src/app/config.py`. Vendor matcher LLM fallback also calls Gemini via `genai.Client`. Model changes should be coordinated across:

1. `src/app/config.py` — update `GEMINI_MODEL` constant
2. `src/app/services/extractor_gemini.py` — uses `GEMINI_MODEL` as default arg
3. `src/app/services/vendor_matcher.py` — instantiates `genai.Client` directly
4. Re-run full test suite and validate extraction quality after any model change

## Jobber GraphQL Field Names

**Status:** Verified against live schema 2026-06-04 ✅

`ExpenseCreateInput` required fields confirmed: `title` (required), `total` (required), `description` (optional), `date` (optional), `jobId` (optional). Live expense creation confirmed: Expense `Z2lkOi8vSm9iYmVyL0V4cGVuc2UvMTg2MTI5NjQ=` created in sandbox account (account_id: 2434520).

OAuth app: "qClerq Invoice Sync" (client_id: e7fbb80b-d38f-45bc-9b83-46721dc5ab4e), scopes: `read_expenses write_expenses`.

For production deployment: re-run `scripts/jobber_oauth.py` against the client's Jobber account to get a fresh access token.

## QuickBooks Expense Account ID

**Status:** Resolved for sandbox — set to "78" (Purchases account)

The QB expense account ID is set via `QB_DEFAULT_EXPENSE_ACCOUNT_ID` in `.env`. For the sandbox (Realm ID: 9341457075838103), this is "78" (Purchases). QB Bill #146 confirmed created successfully 2026-06-04.

For production deployment:
1. Obtain the correct expense account ID from the client's QuickBooks chart of accounts
2. Update `QB_DEFAULT_EXPENSE_ACCOUNT_ID` in `.env` accordingly
3. Verify vendors exist in client QB before processing live invoices

Affected code: `src/app/config.py`, `src/app/services/quickbooks_sync.py`

## Vendor Matcher File Lock Concurrency

**Status:** Single-worker assumption

The vendor matcher uses a file-based lock (`threading.Lock`) to protect writes to the `vendors.json` mapping file. This design is safe only when uvicorn runs in single-worker mode (the current deployment assumption).

If multi-worker deployments are planned:

1. Replace file-based locking with a distributed lock (e.g., Redis)
2. Or switch to a database-backed vendor mapping instead of a JSON file
3. Conduct concurrency testing before deploying with `--workers > 1`

Affected code: `src/app/services/vendor_matcher.py` (lines 13, 81-84)

## Test Coverage

Current test suite covers:
- Sync flow orchestration (Sheets, QB, Jobber independence)
- Validation tier assignment and exception routing
- Extraction (Gemini native PDF vision via mocked `google.genai.Client`)
- Vendor normalization cascade (exact, fuzzy, LLM, fallback)

Not yet covered by automated tests:
- Live QB API integration (mocked in tests)
- Live Jobber GraphQL schema (mocked in tests)
- Live Google Sheets sync (mocked in tests)
- Gmail/Drive intake triggers (n8n configuration — manual test only)

Manual integration testing required before production deployment.

## Adversarial Review 2026-06-04 — Remediation Deferrals

The 2026-06-04 review (`reviews/adversarial-review-2026-06-04.md`) was remediated on branch
`fix/adversarial-review-2026-06-04`. The following items are **partially done or operator-owned**
and must not be assumed complete:

1. **H-1 — Sheets audit row still double-appends on retry.** QuickBooks and Jobber writes are now
   idempotent (check-before-create by `file_hash`/`invoice_number`), but `_run_sync` still appends a
   fresh Invoices row on every call. Full end-to-end idempotency needs an extract-time "claim" row
   (find-or-create by `file_hash`) before the pipeline proceeds. Until then, a retried `/sync` produces
   one extra audit row even though no duplicate Bill/expense is created.

2. **H-1 — Jobber idempotency query is unverified against the live schema.** The `expenses(searchTerm:)`
   lookup in `jobber_sync.py` was not confirmed via GraphiQL. Worst case it false-matches and *skips a
   real expense*. The query failure path is swallowed (degrades to create-anyway), but confirm the
   query before relying on Jobber retry dedup.

3. **H-2 / H-4 — n8n graph changes are unverified beyond `json.load`.** The CFO approval node, exception
   notification node, and the C-1 edge removal have no automated coverage. Run an n8n import + smoke
   test before trusting them. C-1 survives regardless because the backend tier recompute is an
   independent defense; H-2/H-4 have no backend backstop.

4. **H-3 — approver allowlist requires operator wiring.** `/approval-callback` is fail-closed against
   `valid_approvers`. The n8n approval form's "Reviewed By" free-text field must contain an allowlisted
   approver email (recommend converting it to a dropdown of the configured approvers), and
   `VALID_APPROVERS` must be set in `.env`. The `'approver'` literal fallback was removed — an empty
   "Reviewed By" now fails closed (422). `/sync` (auto path) is intentionally exempt from the human
   allowlist: auto-tier is system-approved; its gate is the server-side tier recompute + API key.

5. **C-2 — secret rotation + git-history scrub are operator actions.** See `docs/SECRETS_ROTATION.md`.
   Working-tree secret literals are removed and placeholders are in place, but the backend API key and
   Jobber client secret remain in git history and require a `git filter-repo`/BFG scrub plus rotation
   of all three secrets (backend key, Jobber client secret, n8n JWT). The n8n workflow's
   `REPLACE_WITH_BACKEND_API_KEY`, `CFO_APPROVER_EMAIL_PLACEHOLDER`, and
   `EXCEPTION_NOTIFY_EMAIL_PLACEHOLDER` tokens must be wired (prefer an n8n credential over an inline
   header for the API key).
