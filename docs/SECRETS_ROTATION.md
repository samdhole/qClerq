# Secrets Rotation Runbook

**Last verified:** 2026-06-04
**Trigger:** Adversarial review finding C-2 — live secrets committed to the repo and shared across trust boundaries.
**Audience:** Operator with admin access to the n8n instance, the backend deployment, the Jobber developer app, and the git remote.

> All secret values below are **redacted** to a truncated `prefix…suffix` form. Never paste a full secret into this file, a commit message, a ticket, or a chat. Treat every value listed here as **compromised** and rotate it.

---

## A. What is exposed and where

| # | Secret | Identifier (redacted) | Where it appears | Git history? |
|---|--------|-----------------------|------------------|--------------|
| 1 | Backend API key (gates `/sync`, `/approval-callback`, `/extract`, `/validate`, weekly report) | `vnKak…Ydqs` | `workflows/n8n_invoice_desk.json` (10 occurrences, as inline `X-API-Key` header values) | **YES — committed** |
| 2 | Jobber OAuth client secret | `7945…d6ec` | `scripts/jobber_oauth.py:14` (now reads `JOBBER_CLIENT_SECRET` env var) | **YES — committed** |
| 3 | n8n admin API JWT (full admin on the local n8n instance) | `eyJh…Qjw` | Working-tree-only one-off scripts: `add_api_key_header.py`, `clear_pindata.py`, `fix_approval_callback_body.py`, `fix_localhost.py`, `patch_workflow_api.py`, and the downstream `fix_*`/`reset_*` scripts that read it. **All now read `N8N_API_KEY` env var.** | **NO — untracked working tree only** |

Notes:
- The backend key also appears as a **value n8n injects** into outbound requests, so the same static string spans the n8n→backend trust boundary. Rotating it requires updating **both** the backend's expected key **and** the n8n credential.
- The n8n JWT was copy-pasted as a literal across ~12 scripts. Those literals have been replaced with `os.environ[...]` reads, but the JWT remains valid until rotated and was at risk of being committed (`git add scripts/`).
- The Jobber `CLIENT_ID` (`e7fb…ab4e`) is a public identifier, not a secret. It does not require rotation, only the **client secret** does.

---

## B. Committed-to-history vs working-tree-only

This distinction decides whether you must also **scrub git history** after rotating.

### Committed to git history → rotate **AND** scrub history (Section D)
- **Backend API key** (`vnKak…Ydqs`) — in committed `workflows/n8n_invoice_desk.json`. Reachable in every clone and every prior commit. Rotation alone does not remove it from history.
- **Jobber client secret** (`7945…d6ec`) — in committed `scripts/jobber_oauth.py`. Same exposure.

### Working-tree-only (untracked `??`) → rotate, **no history scrub needed**
- **n8n admin JWT** (`eyJh…Qjw`) — only ever existed in untracked scripts; `git grep` confirms it is in **no** tracked file and **no** commit. After rotation, simply delete or env-ify the local copies (already env-ified). No history rewrite required.

Verify the working-tree-only claim before you skip the scrub:

```bash
# Must print nothing (JWT prefix is not in any tracked blob in history):
git rev-list --all | while read c; do git grep -l "eyJhbGciOiJIUzI1Ni" "$c" 2>/dev/null; done
```

---

## C. Rotation steps (per secret)

Rotate in this order. Each secret is independent except where noted.

### 1. Backend API key (`vnKak…Ydqs`)
1. Generate a new high-entropy key (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Set it on the **backend** as the `api_key` env var (the value `Settings.api_key` reads). Redeploy/restart the backend so `X-API-Key` validation expects the new value.
3. Update the **n8n credential** that injects `X-API-Key` into the backend HTTP Request nodes (`Call /extract API`, `Call /validate API`, `Call /sync API`, `Call /approval-callback`, `Get Weekly Report`). Use an n8n **credential reference**, not an inline header literal, so the secret never re-enters the workflow JSON.
4. Re-test one invoice end-to-end (extract → validate → sync) to confirm both sides agree.
5. Proceed to the history scrub (Section D) — the old key is in committed JSON.

### 2. Jobber OAuth client secret (`7945…d6ec`)
1. In the Jobber developer dashboard for the app (`CLIENT_ID e7fb…ab4e`), **rotate/regenerate the client secret**.
2. Put the new value in `.env` as `JOBBER_CLIENT_SECRET=…` (already git-ignored). Do **not** hardcode it; `scripts/jobber_oauth.py` now reads `os.environ["JOBBER_CLIENT_SECRET"]`.
3. Re-run the OAuth flow (`uv run python scripts/jobber_oauth.py`) to mint a fresh `JOBBER_ACCESS_TOKEN`; update `.env`. Any previously issued access/refresh tokens derived from the old secret should be considered revoked.
4. Proceed to the history scrub (Section D) — the old secret is in committed `scripts/jobber_oauth.py`.

### 3. n8n admin JWT (`eyJh…Qjw`)
1. In the n8n UI: **Settings → API → revoke the existing API key**, then create a new one.
2. Export the new value to your shell/`.env` as `N8N_API_KEY=…` before running any tooling script:
   - bash: `export N8N_API_KEY="<new-jwt>"`
   - PowerShell: `$env:N8N_API_KEY = "<new-jwt>"`
3. All `scripts/*.py` n8n tooling now reads `N8N_API_KEY` and fails fast with a clear message if it is unset — no literal to update.
4. **No git history scrub required** (the old JWT was never committed). Confirm the local literals are gone:
   ```bash
   git grep -n "eyJhbGciOiJIUzI1Ni" ; grep -rn "eyJhbGciOiJIUzI1Ni" scripts/ || echo "no JWT literals remain"
   ```

---

## D. Git history scrub (for the two committed secrets only)

Rotating without scrubbing leaves the old values in every clone and every prior commit. Scrub **after** rotating (so a leaked-but-rotated value is merely useless, not still live during the rewrite).

> A history rewrite changes commit hashes. Coordinate with anyone who has a clone — they must re-clone or hard-reset. Take a backup branch/bundle first: `git bundle create ../qclerq-backup.bundle --all`.

### Option 1 — git filter-repo (recommended)
```bash
# Install (uv-managed):
uv tool install git-filter-repo    # or: pipx install git-filter-repo

# Create a replacements file mapping each committed secret to a placeholder.
# Use the FULL secret values here (this file stays local; never commit it):
cat > ../secret-replacements.txt <<'EOF'
vnKak<...full backend key...>Ydqs==>REDACTED_BACKEND_API_KEY
7945<...full jobber secret...>d6ec==>REDACTED_JOBBER_CLIENT_SECRET
EOF

git filter-repo --replace-text ../secret-replacements.txt

# Re-add the remote (filter-repo drops it), then force-push the rewritten history:
git remote add origin <REMOTE_URL>
git push --force --all origin
git push --force --tags origin

# Clean up:
rm ../secret-replacements.txt
```

### Option 2 — BFG Repo-Cleaner
```bash
# Put the two full secret values (one per line) in a local file:
printf '%s\n%s\n' '<full backend key>' '<full jobber secret>' > ../secrets.txt

bfg --replace-text ../secrets.txt        # replaces matches with ***REMOVED***
git reflog expire --expire=now --all && git gc --prune=now --aggressive
git push --force
rm ../secrets.txt
```

### Post-scrub verification
Substitute the real (now-rotated) values for `$BACKEND_KEY` / `$JOBBER_SECRET` in your shell — do not write them into this file.
```bash
# Each must print nothing (no commit still contains the old value):
git rev-list --all | while read c; do git grep -l "$BACKEND_KEY"  "$c" 2>/dev/null; done
git rev-list --all | while read c; do git grep -l "$JOBBER_SECRET" "$c" 2>/dev/null; done
```
If the repo is on a host that caches refs (GitHub), also delete/recreate any forks and ask the host to garbage-collect stale unreachable commits.

---

## E. Prevent recurrence

1. **Secrets live in env / a credential store, never in tracked files.** Backend reads `api_key` from env; n8n injects via a **credential reference**, not an inline header literal in the workflow JSON.
2. **`.gitignore` blocks secret material** — `.env`, `*.env`, `scripts/*.local.*`, `*credentials*.json`, `service-account*.json`, `*.pem`, `*.key`, `*.p12` (see repo `.gitignore`). `.env.example` stays tracked as the documented template.
3. **Per-boundary scoping:** do not reuse one static string across the n8n↔backend boundary long-term; prefer distinct, individually-rotatable credentials per caller.
4. **Pre-commit secret scanning** (e.g. `gitleaks`) so a literal secret cannot be committed again:
   ```bash
   # .pre-commit-config.yaml entry, illustrative:
   #   - repo: https://github.com/gitleaks/gitleaks
   #     rev: v8.x
   #     hooks: [{ id: gitleaks }]
   ```
5. **Sanitize workflow exports** before committing (strip inline credential values, `shared`/`project` blocks, and instance IDs — see review finding L-1).
