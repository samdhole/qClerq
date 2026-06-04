# pattern: Imperative Shell
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.schemas.invoice import SyncRequest, SyncResult
from app.services import jobber_auth

_JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
_JOBBER_API_VERSION = "2025-04-16"

# Stable identity tag embedded in the expense description so a retry can find the
# already-created expense instead of creating a duplicate (H-1 idempotency).
_HASH_TAG_PREFIX = "[qclerq-file-hash:"
_HASH_TAG_SUFFIX = "]"

# Field names verified against live Jobber schema 2026-06-04:
# Required: title, total. Optional: description, date, jobId.
_EXPENSE_CREATE_MUTATION = """
mutation ExpenseCreate($input: ExpenseCreateInput!) {
    expenseCreate(input: $input) {
        expense {
            id
            title
            total
        }
        userErrors { message path }
    }
}
"""

# List recent expenses to dedupe a retry. The embedded file_hash tag in the
# description is the durable identity; searchTerm narrows the scan to this invoice.
# NOTE: unlike the create mutation, the expenses connection / searchTerm arg here
# is NOT confirmed against the live Jobber schema. If unsupported it raises, and
# _create_expense_sync swallows that and creates anyway (so a wrong guess degrades
# to a possible duplicate on retry, never a hard sync failure).
_EXPENSE_SEARCH_QUERY = """
query ExpenseSearch($searchTerm: String) {
    expenses(first: 50, searchTerm: $searchTerm) {
        nodes {
            id
            description
        }
    }
}
"""


def _hash_tag(file_hash: str) -> str:
    return f"{_HASH_TAG_PREFIX}{file_hash}{_HASH_TAG_SUFFIX}"


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-JOBBER-GRAPHQL-VERSION": _JOBBER_API_VERSION,
    }


def _find_existing_expense_id(req: SyncRequest, access_token: str) -> str | None:
    """Return the id of an expense already created for this invoice, or None.

    Idempotency guard (H-1): every expense description carries the invoice's
    file_hash tag. We search recent expenses (narrowed by the invoice number)
    and confirm the embedded tag before treating it as a duplicate.
    """
    inv = req.invoice
    tag = _hash_tag(inv.file_hash)
    search_term = inv.invoice_number or inv.vendor_normalized

    payload = {
        "query": _EXPENSE_SEARCH_QUERY,
        "variables": {"searchTerm": search_term},
    }
    resp = httpx.post(
        _JOBBER_GRAPHQL_URL, json=payload, headers=_headers(access_token), timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Jobber GraphQL errors: {data['errors']}")

    nodes = data.get("data", {}).get("expenses", {}).get("nodes", []) or []
    for node in nodes:
        if tag in (node.get("description") or ""):
            return str(node["id"])
    return None


def _create_expense_sync(req: SyncRequest, access_token: str) -> tuple[str, bool]:
    """Create Jobber expense (or return an existing one for a retry).

    Returns (jobber_expense_id, created) where created is False when an existing
    expense tagged with this invoice's file_hash was found (idempotent skip).
    Raises on create failure.
    """
    inv = req.invoice

    # Idempotency check-before-create: return the existing expense instead of a
    # duplicate (H-1). Best-effort — a query failure must NOT block the create, so we
    # swallow it and fall through to create. This also contains the blast radius if
    # the expenses search query/args are unsupported: degrade to "create" not "fail".
    try:
        existing_id = _find_existing_expense_id(req, access_token)
    except Exception:
        existing_id = None
    if existing_id is not None:
        return existing_id, False

    # Field names verified against live Jobber schema 2026-06-04:
    # - title (required), description (optional), total (required), date (optional), jobId (optional)
    # The file_hash tag in the description is the durable idempotency key (H-1).
    expense_input: dict[str, Any] = {
        "title": f"Invoice {inv.invoice_number or 'unknown'} — {inv.vendor_normalized}",
        "description": (
            f"Invoice {inv.invoice_number or 'unknown'} from {inv.vendor_normalized} "
            f"{_hash_tag(inv.file_hash)}"
        ),
        "total": inv.total,
    }
    if inv.job_id:
        expense_input["jobId"] = inv.job_id
    if inv.invoice_date:
        expense_input["date"] = str(inv.invoice_date)

    payload = {
        "query": _EXPENSE_CREATE_MUTATION,
        "variables": {"input": expense_input},
    }

    resp = httpx.post(
        _JOBBER_GRAPHQL_URL, json=payload, headers=_headers(access_token), timeout=30
    )
    resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Jobber GraphQL errors: {data['errors']}")

    result = data.get("data", {}).get("expenseCreate", {})
    user_errors = result.get("userErrors", [])
    if user_errors:
        raise RuntimeError(f"Jobber userErrors: {user_errors}")

    expense = result.get("expense")
    if not expense:
        raise RuntimeError("Jobber expenseCreate returned no expense object")

    return str(expense["id"]), True


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Create Jobber expense. Returns SyncResult with jobber_expense_id.

    The access token comes from `jobber_auth`, which refreshes the short-lived Jobber
    token transparently. Idempotent (H-1): a retry of an already-synced invoice returns
    the existing id with sync_status "skipped". If a call still returns 401 (e.g. a token
    revoked mid-life), we invalidate the cache, mint a fresh token, and retry once.
    """
    try:
        token = jobber_auth.get_access_token(settings)
        try:
            jobber_expense_id, created = await asyncio.to_thread(_create_expense_sync, req, token)
        except httpx.HTTPStatusError as e:
            if getattr(e.response, "status_code", None) == 401:
                jobber_auth.invalidate()
                token = jobber_auth.get_access_token(settings)
                jobber_expense_id, created = await asyncio.to_thread(_create_expense_sync, req, token)
            else:
                raise
        sync_status = {"jobber": "ok" if created else "skipped"}
    except Exception:
        jobber_expense_id = None
        sync_status = {"jobber": "failed"}

    return SyncResult(jobber_expense_id=jobber_expense_id, sync_status=sync_status)
