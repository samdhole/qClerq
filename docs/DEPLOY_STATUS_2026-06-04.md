# Ship Status & Operator Handoff — 2026-06-04

Branch: `fix/adversarial-review-2026-06-04` (commits `942e053`, `4421569`). **Not merged, not deployed.**

## ✅ Verified LIVE (real running stack, captured evidence)

| Fix | Evidence |
|-----|----------|
| Backend key **rotated** | Old leaked key `vnKak…Ydqs` now returns **401**; new key only in `.env` + n8n credential |
| **C-1** backend guard | `/sync` with `$5000`+`approval_tier:"auto"` → **422** ("routes to 'manager' tier"); `/approval-callback` auto → 422 |
| **H-3** fail-closed approver | unlisted `hacker@evil.com` → **422** |
| **M-2** `/validate` | clean invoice → 200, live Sheets dedupe read works |
| **C-1** graph (live n8n) | `approval-callback` no longer edges into `/sync`; CFO node present; orphan hash node gone (API + Chrome) |
| n8n→backend **credential auth** | exec **#116** success: `/extract` + `/validate` authenticated via the new credential |
| **H-4** exception notify | exec #116: low-confidence invoice → exception path → **Notify Exception Reviewer node sent its email** (Gmail threadId returned) |

## ⚠️ NOT verified at runtime (don't assume these work yet)

- **The core C-1 scenario end-to-end** — an *approved* (manager/CFO) invoice flowing through n8n and creating **exactly one** QB bill / Jobber expense — was **not run live**. It is covered by structure (edge removed) + a backend unit test (spoof→422), **not** by watching an approved invoice sync once.
- **H-1 idempotency at runtime** — **0% live-verified.** No QuickBooks or Jobber write executed in any test. The Jobber dedup query is also unconfirmed against the live schema (worst case: false-match skips a real expense).
- **QuickBooks / Jobber sandbox tokens are likely DEAD.** The Jobber token in `.env` expires ~mid-2026 (a fresh Jobber OAuth callback tab was open in the browser, suggesting a re-auth in progress). Refresh both before trusting any sync.

## 🔴 The running system is EPHEMERAL — read this

- The backend on `:9100` is a **background process** started by the tooling session, running **un-merged branch code**. It **will die when the session ends.**
- The original backend process was stopped and replaced. `git checkout master` reverts all code.
- **To make it last:** (1) run the backend as a real persistent service from this branch (e.g. `uv run uvicorn app.main:app --host 127.0.0.1 --port 9100` under a service manager, CWD = repo root so it loads `.env`); (2) **merge `fix/adversarial-review-2026-06-04` and deploy.** Until then, "fixed + verified" is temporary.

## 📋 Operator to-do (in priority order)

1. **Persist + deploy:** run the backend as a service from this branch; merge the branch.
2. **Tooling scripts need env vars:** the env-ified `scripts/*.py` now read `N8N_API_KEY` and `BACKEND_API_KEY` from the environment — **set these** or those scripts will exit with a clear error. (`BACKEND_API_KEY` = the new value in `.env`'s `API_KEY`; `N8N_API_KEY` = your n8n public API key.)
3. **Third-party secret rotation (I cannot do these):** rotate the **Jobber client secret** and **Google service account** in their consoles; refresh QB/Jobber sandbox tokens. Then **scrub git history** for the Jobber secret + the (now-dead) old backend key — see `docs/SECRETS_ROTATION.md`.
4. **Transcript hygiene:** the assistant session that did this work loaded the full `.env` into its context (Google private key, QB/Jobber/Slack tokens). **If you ever share that transcript, rotate those too.**

## Reference

- n8n credential created: **"qClerq Backend API Key"** (Header Auth, holds the rotated key). Referenced by the 5 backend HTTP nodes.
- Email wiring: manager → `enigman.kk+manager@gmail.com`, CFO → `enigman.kk+cfo@gmail.com`, exceptions → `enigman.kk+exceptions@gmail.com`. `VALID_APPROVERS` updated to match.
- Automated tests: **125 passing** (`uv run pytest src/app/tests`).
