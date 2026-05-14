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

**Status:** Unverified against live schema

The `ExpenseCreateInput` field names used in `jobber_sync.py` are derived from Jobber API documentation but have not been validated against the live GraphQL schema. Before deploying to production:

1. Verify field names match the current Jobber API schema
2. Test with a sample expense creation in a non-production Jobber instance
3. Update field mappings if schema changes are discovered

Affected code: `src/app/services/jobber_sync.py`

## QuickBooks Expense Account ID

**Status:** Configurable, defaults to "1"

The QB expense account ID used when creating bill line items is now configurable via the `QB_DEFAULT_EXPENSE_ACCOUNT_ID` environment variable (defaults to "1"). This value should be verified and customized per client:

1. Obtain the correct expense account ID from the client's QuickBooks setup
2. Set `QB_DEFAULT_EXPENSE_ACCOUNT_ID` in `.env` to match their account structure
3. Test bill creation with a sample invoice to confirm proper account routing

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
