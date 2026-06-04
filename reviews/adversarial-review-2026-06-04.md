# Adversarial Code & Design Review

**Date:** 2026-06-04
**Scope:** End-to-end pipeline — `src/app/` (services, schemas, api, config), `workflows/n8n_invoice_desk.json`, `scripts/`
**Reviewer:** Claude Code (adversarial pass)
**Prior review:** `reviews/adversarial-review-2026-05-13.md` (Python-only; did not examine the n8n graph)

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 2 |
| HIGH | 4 |
| MEDIUM | 4 |
| LOW | 4 |
| **Total** | **14** |

The Python layer has materially improved since 2026-05-13 — most prior code-level defects are genuinely fixed (math checks, QBOSQL escaping, schema hygiene, size/magic-byte guards). **The damage has moved up a layer.** The system's stated core value — *trusted intake + validation + approval + proof trail* — is now broken not in the services but in the **orchestration graph that wires them together** and in **how the system handles its own secrets**. The single most important finding (C-1) means every human-approved invoice is posted to QuickBooks and Jobber **twice**, and lands in the audit sheet as **two contradictory rows** — one crediting the real approver, one stamped `auto-approved`. For a product whose entire pitch is the proof trail, this is the worst possible failure mode.

This review deliberately challenges the *approach*, not just defects. The recurring theme: the deterministic core is solid, but the system trusts its own plumbing (n8n edges, config defaults, version-controlled secrets) far more than it trusts a vendor's PDF — and the plumbing has not earned that trust.

---

## CRITICAL

---

### C-1 — Approval gate double-fires and writes a contradictory audit trail

**Files:** `workflows/n8n_invoice_desk.json` (connections: `Check Approval Decision` → `Call /approval-callback` → `Call /sync API`); `src/app/api.py:223-250`

The n8n graph wires the manager/CFO approval path as:

```
Route by Approval Tier ─(manager|cfo)→ Send Invoice for Approval
  → Check Approval Decision ─(Yes)→ Call /approval-callback
      → Call /sync API
```

Both `/approval-callback` and `/sync` end in the **same** `_run_sync(req, settings)` — the full three-target write (Sheets + QuickBooks + Jobber). So for **every manager- or CFO-approved invoice**:

- **Two QuickBooks Bills** are created.
- **Two Jobber expenses** are created.
- **Two Sheets rows** are appended — and they disagree.

The `/approval-callback` row carries the real approver and tier (`approved_by = "<Reviewed By>"`, `approval_tier = "manager"`). But the chained `/sync` call sends a **hardcoded body** (`workflows/n8n_invoice_desk.json:627`):

```js
JSON.stringify({ invoice: ..., approved_by: 'auto-approved',
                 approval_notes: '', approved_at: ..., approval_tier: 'auto' })
```

So the second row records the same invoice as **`auto-approved` / `auto`-tier** — i.e., the system's own audit log now contains a row asserting that a human-gated $5,000+ invoice was auto-approved with no reviewer.

**Why it fires under default config:** `/sync` calls `_check_approved_by`, but `settings.valid_approvers` defaults to `[]` (`config.py:46`), so the guard at `api.py:144` is skipped and `'auto-approved'` passes. The `approval_tier == "auto"` check (`api.py:245`) also passes because the body hardcodes `auto`. Nothing stops the second sync.

**Impact:**
- Duplicate financial records in two external accounting systems on every approved invoice (real money, real reconciliation pain).
- The proof trail — the product's core value — contains two conflicting rows per approved invoice, one of which actively misattributes approval.
- No idempotency key anywhere (see H-1), so there is no downstream defense.

**Fix:** Remove the `Call /approval-callback → Call /sync API` edge. `/approval-callback` is already the terminal sync for the manager/CFO path. The auto path correctly goes `Route by Approval Tier ─(auto)→ Call /sync API` and must remain the *only* caller of `/sync`. Add a server-side guard: reject `/sync` when the invoice's computed tier ≠ `auto` regardless of the body's self-declared tier, and stop trusting a client-supplied `approval_tier` for the security decision.

---

### C-2 — Live secrets committed to the repository and shared across trust boundaries

**Files:** `workflows/n8n_invoice_desk.json` (tracked), `scripts/jobber_oauth.py` (tracked), plus untracked `scripts/*.py`

Prior review H-2 ("no authentication") is *nominally* fixed — every protected route now enforces `X-API-Key` (`api.py:30-39`). But the fix is defeated by how the key is stored:

**Committed to git history (require rotation + history scrub):**
- **Backend API key** `vnKak…Ydqs` is hardcoded in the tracked `workflows/n8n_invoice_desk.json` (10 occurrences) — the single static secret that gates `/sync`, `/approval-callback`, and `/extract`. It is the *only* thing protecting financial-write endpoints, and it is in version control.
- **Jobber OAuth client secret** `7945…d6ec` is hardcoded in tracked `scripts/jobber_oauth.py:14`.

**Working tree only — untracked `??` (require `.gitignore` + rotation, no history scrub):**
- The **n8n admin API JWT** (`eyJhbGciOiJIUzI1NiIs…`) appears in plaintext in `scripts/clear_pindata.py`, `add_api_key_header.py`, `fix_localhost.py`, `patch_workflow_api.py`, `fix_approval_callback_body.py`, and others. This JWT is full admin access to the n8n instance. It is one `git add scripts/` away from being committed.

**Design problem, not just hygiene:** the backend API key is *also* the value n8n injects, so the same static string spans the n8n→backend boundary, and the n8n admin JWT is copy-pasted across a dozen one-off scripts. There is no rotation story, no per-caller scoping, and no separation between "tooling can reach n8n" and "n8n can reach the backend."

**Fix:**
1. Rotate **all three** secrets now (backend key, Jobber client secret, n8n JWT) — assume compromise.
2. Scrub the two committed secrets from git history (`git filter-repo` or BFG); rotating without scrubbing leaves them in every clone.
3. Move secrets to env/credential store; reference them in n8n via credentials, not inline header values. Add `scripts/` secret files and any `.env` to `.gitignore`.
4. Pre-commit secret scanning (e.g., `gitleaks`) so this cannot recur.

---

## HIGH

---

### H-1 — Duplicate detection reads from its own sink; cannot catch same-batch duplicates

**Files:** `src/app/api.py:86-106`, `src/app/services/sheets_sync.py:143-187`, `src/app/services/dedupe.py`

The dedupe design (prior review's headline H-1) is now *implemented* — both semantic keys exist (`check_semantic_duplicate`) and the API takes the worse of hash/semantic risk. Good. But the **source of truth is the sink**: `get_known_invoice_data` reads known hashes/invoices from the Sheets **Invoices** tab, and a row is only written to that tab **at sync time**, at the very end of the pipeline.

Consequences:
- **Read-before-write race.** Two submissions of the same invoice (same email retried, same file dropped twice, a vendor re-sending) that enter the pipeline before either has synced both see an empty/stale Invoices tab → both score `duplicate_risk = "none"` → both validate clean → both sync. The guarantee fails precisely in its most common real-world case.
- **No idempotency key.** If the network drops after QuickBooks creates a Bill but before the response returns, a retry creates a second Bill. Nothing dedupes at the QB/Jobber write.
- Combined with C-1, the system has *two* independent duplicate-creation paths and *zero* idempotency defenses.

**Fix:** Give the pipeline a synchronous claim step — write a `pending` Invoices row (or a separate dedupe ledger keyed by `file_hash` and `(vendor, invoice_number)`) **at extract time**, before approval, so concurrent submissions collide on a real record. Add an idempotency key (e.g., `file_hash`) to QB/Jobber writes and check-before-create.

---

### H-2 — The CFO approval tier does not exist in practice

**Files:** `src/app/services/approval_router.py`, `workflows/n8n_invoice_desk.json:352-405`, `src/app/config.py:27-28`

`approval_router.route()` correctly computes three tiers (`auto` / `manager` / `cfo`), and `config.py` defines both `manager_email` and `cfo_email`. But in the workflow, **both** the `manager` and `cfo` outputs of `Route by Approval Tier` connect to the **same single node** `Send Invoice for Approval`, whose recipient is **hardcoded** to `enigman.kk@gmail.com` (`sendTo`, line 355). `manager_email` and `cfo_email` are referenced nowhere in application code (only in tests).

So a $600 invoice and a $60,000 invoice receive identical handling, routed to the same inbox. The CFO escalation tier — the control that justifies the threshold design — is decorative. Anyone auditing the config would reasonably believe high-value invoices escalate to a CFO; they do not.

**Fix:** Route `cfo`-tier to a distinct node addressed to `cfo_email`, and drive both approval nodes from config rather than a hardcoded address. If single-approver is intentional for this phase, say so in `KNOWN_GAPS.md` and remove `cfo_email` to avoid implying a control that isn't wired.

---

### H-3 — Approver identity is unverified by default (prior H-3 only partially fixed)

**Files:** `src/app/api.py:140-148`, `src/app/config.py:46`, `workflows/n8n_invoice_desk.json:656`

The prior fix added an allowlist check — but it is gated on `settings.valid_approvers` being **non-empty**, and the default is `[]` (`config.py:46`). With the default config, `_check_approved_by` enforces only "non-empty string," exactly the prior weakness. The n8n callback supplies `approved_by` from the free-text **"Reviewed By"** form field, falling back to the literal string `'approver'` (`json: $json.data['Reviewed By'] || ... || 'approver'`). So by default, any value — including a typo or the fallback literal — is accepted as the approver of record.

This also feeds C-1: the empty allowlist is exactly why the chained `/sync` accepts `'auto-approved'`.

**Fix:** Make `valid_approvers` required (or fail-closed when empty for write endpoints). Validate the `Reviewed By` value against it server-side, and drop the `'approver'` fallback — an unidentified approver should fail, not default to a placeholder.

---

### H-4 — Exception invoices have no escalation, notification, or resolution path

**Files:** `workflows/n8n_invoice_desk.json` (`Route Exceptions` → `Write to Exceptions Sheet`, terminal), `src/app/services/validator.py`

When validation produces any exception (`is_clean == false`) — including a `duplicate_risk = "possible"` flag or a `low_confidence` flag — the workflow routes the invoice to `Write to Exceptions Sheet` (status `"open"`) and **stops**. That node has no onward connection: the invoice is never approved, never synced, and **no human is notified in real time**.

A legitimate edge case is enough to trigger this: two genuine invoices from the same vendor, same total, same day (within ±3 days) score `"possible"` per `check_semantic_duplicate`. One of them is now stranded in a sheet with `status = "open"` and no one is paged. Exceptions *do* surface later in the weekly report's `exception_rate` aggregate (`report_generator.py:84`), so this is not fully silent — but "appears in a weekly average" is not an escalation path. Real invoices can go unpaid until someone happens to read the Exceptions tab.

**Fix:** Add a real-time notification on exception (email/Slack to the manager) and a resolution path (a way to clear, re-route, or force-approve an `open` exception). Distinguish "needs human judgment" exceptions (duplicate_risk, low_confidence) from "reject" exceptions (math errors) — they deserve different routing.

---

## MEDIUM

---

### M-1 — 2% relative math tolerance makes validation weak for large invoices (documented choice, not a bug)

**Files:** `src/app/services/validator.py:30-76`, `src/app/schemas/validation.py:12-14`

The math, line-items-vs-subtotal, and per-line checks all use `tolerance = max(0.02, abs(amount) * 0.02)` — a flat **2%**. This is the documented contract (`CONTEXT.md`: "2% or $0.02, whichever is larger"), so it is a deliberate design decision, but an aggressive one for financial reconciliation: a $10,000 invoice can be internally inconsistent by up to **$200** and still validate "clean." Because the same 2% is applied independently at three levels, the blind spots compound.

Important calibration: auto-sync is capped at `tier_1_max = $500`, so the *auto-sync* blind spot is only ~$10. The $200 case is CFO-tier and (in principle) human-reviewed — so this is "validation is weaker than its precision implies," not an auto-sync exploit. Still worth challenging: a 2% tolerance on a $50k invoice is a $1,000 window the deterministic layer will not flag, on exactly the invoices where a human reviewer is most likely to trust the green checkmark.

**Fix (if desired):** tighten the relative tolerance (e.g., 0.5%) or cap the absolute tolerance (e.g., min(2%, $5)). At minimum, document the residual window in the approval email so reviewers don't over-trust "math: ok."

---

### M-2 — Validator's `dedupe_result` can be caller-controlled on the direct `/validate` path

**Files:** `src/app/api.py:111-122`, `src/app/services/validator.py:103-114`

Prior M-1 is mostly fixed: `validate()` now takes an explicit `dedupe_result` param instead of reading `inv.duplicate_risk` internally. But the `/validate` endpoint feeds that param **from `invoice.duplicate_risk`** (`api.py:121`) — the same schema field the LLM populates. In the real n8n flow this field carries the deterministic value computed by `/extract`, so it's fine in practice. But a direct caller hitting `/validate` controls `duplicate_risk` entirely and can set it to `"none"` to suppress the duplicate exception. The deterministic guarantee holds only because of call ordering, not because the endpoint enforces it.

**Fix:** Have `/validate` recompute (or refuse to trust) `duplicate_risk` rather than reading it from the request body, or document that `/validate` is not a security boundary and must only be called after `/extract`.

---

### M-3 — `vendor_raw` (attacker-controlled) becomes an unbounded key in the trusted vendor store

**File:** `src/app/services/vendor_matcher.py:88-93`

Prior C-2 is genuinely fixed — the LLM result is now constrained (`if llm_result and llm_result in canonical_names`), so a poisoned *value* can no longer enter `vendors.json`. But the **key** is still `vendor_raw`: `mapping[vendor_raw] = llm_result`. `vendor_raw` comes straight from the PDF via the LLM and is unbounded and attacker-influenced. A vendor can submit many spelling variants ("Acme", "Acme ", "ACME Inc", …) and each successful LLM match writes a new key. `vendors.json` is loaded into memory on cold start (`_load_vendors`), so unbounded growth is a slow resource/poisoning vector, and the file is now also a sink for arbitrary attacker strings (as keys).

**Fix:** Normalize/whitelist the key before persisting (e.g., store under a canonicalized form), cap the mapping size, and treat auto-promotion as a suggestion queue for human review rather than an immediate trusted write.

---

### M-4 — Orphaned nodes and stale extraction prompt (drift between design and reality)

**Files:** `workflows/n8n_invoice_desk.json` (`Compute File Hash` node), `src/app/prompts/invoice_extraction.md`, `src/app/services/sheets_sync.py:196-219`

Three drift artifacts that erode confidence the system is what it claims:
- **`Compute File Hash`** node exists but is bypassed in the Drive path (`Download Invoice PDF → Call /extract API` directly); the backend recomputes the hash anyway. Dead node implying a control that runs elsewhere.
- **`invoice_extraction.md`** still says "Extract all structured fields from the invoice **text** provided" and instructs the model to leave `file_hash`/`file_name` as `""` — but extraction is now native-PDF vision and those fields were removed from the function schema (`extractor_gemini.py`). The prompt describes a pipeline that no longer exists.
- **`sheets_sync.sync()`** (lines 196-219) is an unused QB/Jobber-style wrapper that would append a *second* Invoices row if ever wired; `_run_sync` calls `write_invoice_row` directly. Dead code that duplicates the write path is a foot-gun given C-1.

**Fix:** Delete the orphaned node and the dead `sync()` wrapper; rewrite the prompt to match native-PDF extraction and the current schema.

---

## LOW

---

### L-1 — Third-party identity embedded in the committed workflow

**File:** `workflows/n8n_invoice_desk.json:1099-1117`

The `shared`/`project` block hardcodes an unrelated person as workflow owner: `"name": "Sam Dhole <samdhole95@gmail.com>"`, with internal n8n project/creator IDs. This is committed. At minimum it leaks a third party's email and the n8n instance's internal IDs; it also suggests the exported blueprint was copied from another account without scrubbing.

**Fix:** Strip `shared`, `project`, credential IDs, and `staticData` from any committed workflow export; commit a sanitized blueprint only.

---

### L-2 — Config validation is lazy; missing required secrets fail at first request, not boot

**File:** `src/app/config.py:71-73`

`get_settings()` is `@lru_cache`-constructed on first use, and `create_app()` (`main.py`) does not instantiate `Settings` at startup. A missing required env var (`api_key`, `gemini_api_key`, `sheet_id`, `google_service_account_json`) therefore surfaces as a 500 on the first request that touches settings, not as a fast boot failure. Operators can deploy a misconfigured service that looks healthy until traffic arrives.

**Fix:** Instantiate `get_settings()` in the lifespan startup so misconfiguration fails loudly at boot.

---

### L-3 — `application/octet-stream` still accepted at the content-type gate

**File:** `src/app/api.py:54`

The content-type allowlist still includes `application/octet-stream` (the generic binary type). This is now mitigated by the magic-byte check (`pdf_bytes[:4] != b"%PDF"`, line 67) and the size cap, so the residual risk is small — a non-PDF still gets read into memory and hashed before rejection. Noted as resolved-in-practice; tightening the content-type gate would let obviously-wrong uploads fail before the read.

---

### L-4 — Auto-tier `/sync` trusts a client-declared `approval_tier` for its security check

**File:** `src/app/api.py:236-250`

`/sync` decides whether to proceed based on `req.approval_tier` from the request body (`if req.approval_tier != "auto": reject`). The tier is the *caller's* claim, not a server-side recomputation from `req.invoice.total`. A caller can submit a high-value invoice with `approval_tier: "auto"` and a non-allowlisted (or, by default, any) `approved_by` and bypass the manager/CFO path entirely. This is the same root weakness C-1 exploits accidentally; here it is an intentional-bypass vector.

**Fix:** Recompute the tier server-side from `invoice.total` and the configured thresholds; reject `/sync` if the computed tier is not `auto`. Never let the body's self-declared tier be the gate.

---

## Prior-Issue Disposition (vs. 2026-05-13)

Adversarial does not mean ungenerous — most of the prior code-level fixes genuinely hold. The unresolved ones matter precisely because the easy wins were taken.

| Prior | Issue | Status |
|-------|-------|--------|
| C-1 | Prompt injection via raw PDF text | **Mitigated** — native-PDF vision (no text interpolation), untrusted-content system suffix, `tool_choice=ANY`. String fields still flow downstream (see M-3). |
| C-2 | LLM vendor resolution writes unvalidated canonical | **Fixed (value)** — `llm_result in canonical_names` enforced. Key is still attacker-controlled (M-3). |
| C-3 | QBOSQL injection in vendor lookup | **Fixed** — `vendor_name.replace("'", "''")` at `quickbooks_sync.py:34`. |
| H-1 | Semantic dedupe not implemented | **Implemented, but architecturally undermined** — reads from the sink; same-batch races pass (H-1 above). |
| H-2 | No authentication | **Fixed on paper, undermined in practice** — `X-API-Key` enforced, but the key is committed to the repo and shared across boundaries (C-2). |
| H-3 | `approved_by` unvalidated free text | **Partially fixed** — allowlist exists but defaults empty → permissive (H-3 above). |
| H-4 | Sheets row written with empty sync_status | **Fixed** — explicit `pending` status, then backfill. |
| H-5 | TOCTOU on Sheets row index | **Fixed (primary path)** — `updatedRange` parsed from append response; positional fallback remains for non-concurrent path. |
| H-6 | `file_hash`/`file_name` in LLM schema | **Fixed** — removed from function schema; injected server-side. |
| H-7 | line-items vs subtotal unchecked | **Fixed** — `validator.py:46-59`. |
| H-8 | quantity × unit_price unchecked | **Fixed** — `validator.py:61-76`. |
| M-1 | LLM self-reported confidence/dup as gate | **Mostly fixed** — explicit `dedupe_result` param; `/validate` still trusts body field (M-2 above). |
| M-2 | service-account defaults to `"{}"` | **Fixed** — required field + validator checks `client_email`/`private_key`. |
| M-3 | No file size limit | **Fixed** — `max_upload_bytes` 20 MB, 413 on exceed. |
| M-5 | `/sync` no tier access control | **Partially fixed** — `/sync` now rejects non-auto, but trusts client-declared tier (L-4). |
| M-7 | exception_rate could exceed 1.0 | **Fixed** — counts distinct `file_hash`. |
| L-2 | `assert` for integrity check | **Fixed** — raises `ValueError`. |
| L-5 | octet-stream accepted as PDF | **Mitigated** — magic-byte check added; content-type still permissive (L-3). |

---

## Design Challenges (the part that questions the approach)

1. **The proof trail is asserted, not enforced.** The audit row is written by the same code path that performs the sync, with no reconciliation against what QuickBooks/Jobber actually recorded. C-1 produces two contradictory rows; nothing detects the contradiction. A trustworthy proof trail would be *derived from* confirmed external-system state (read back the QB Bill ID and verify), not from the orchestrator's optimistic local view.

2. **The security boundary is in the wrong place.** Every write endpoint trusts caller-supplied fields (`approval_tier`, `approved_by`, `duplicate_risk`) for decisions those fields are supposed to *gate*. The server should recompute tier from `total`, recompute duplicate risk itself, and verify approver identity — never accept the client's claim about the very thing being checked. Right now the n8n graph is effectively inside the trust boundary, and the graph has a duplicate-write edge.

3. **No idempotency anywhere in a financial pipeline.** Between C-1 (double edge), H-1 (read-from-sink dedupe), and retry-on-network-drop, there are at least three ways to create duplicate Bills, and zero defenses. An idempotency key keyed on `file_hash` at the QB/Jobber write would neutralize all three at once and is the highest-leverage single fix after C-1.

4. **Secret management has no model.** Static keys, copied across a dozen scripts, committed to the repo, shared across the n8n↔backend boundary. The auth feature exists; the secret hygiene to make it meaningful does not. This is the gap between "we added an API key" and "the API key protects anything."

---

## Recommended Fix Order

1. **C-1** — delete the `approval-callback → /sync` edge; add server-side tier recomputation on `/sync`. (Stops duplicate financial records today.)
2. **C-2** — rotate all three secrets, scrub the two committed ones from history, move to a credential store, add secret scanning.
3. **H-1 / Design #3** — add an idempotency key on QB/Jobber writes and a synchronous extract-time dedupe claim.
4. **H-3 / L-4** — fail-closed approver allowlist; recompute tier server-side, never trust the body.
5. **H-2, H-4** — wire real CFO escalation; add real-time exception notification + resolution path.
6. **M-/L-** — config-boot validation, prompt/dead-node cleanup, tolerance review, sanitized workflow export.
