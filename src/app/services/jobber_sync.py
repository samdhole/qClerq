from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.schemas.invoice import SyncRequest, SyncResult

_JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
_JOBBER_API_VERSION = "2025-04-16"

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


def _create_expense_sync(req: SyncRequest, access_token: str) -> str:
    """Create Jobber expense and return jobber_expense_id. Raises on failure."""
    inv = req.invoice

    # Field names verified against live Jobber schema 2026-06-04:
    # - title (required), description (optional), total (required), date (optional), jobId (optional)
    expense_input: dict[str, Any] = {
        "title": f"Invoice {inv.invoice_number or 'unknown'} — {inv.vendor_normalized}",
        "description": f"Invoice {inv.invoice_number or 'unknown'} from {inv.vendor_normalized}",
        "total": inv.total,
    }
    if inv.job_id:
        expense_input["jobId"] = inv.job_id
    if inv.invoice_date:
        expense_input["date"] = str(inv.invoice_date)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-JOBBER-GRAPHQL-VERSION": _JOBBER_API_VERSION,
    }
    payload = {
        "query": _EXPENSE_CREATE_MUTATION,
        "variables": {"input": expense_input},
    }

    resp = httpx.post(_JOBBER_GRAPHQL_URL, json=payload, headers=headers, timeout=30)
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

    return str(expense["id"])


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Create Jobber expense. Returns SyncResult with jobber_expense_id."""
    try:
        jobber_expense_id = await asyncio.to_thread(
            _create_expense_sync, req, settings.jobber_access_token
        )
        sync_status = {"jobber": "ok"}
    except Exception:
        jobber_expense_id = None
        sync_status = {"jobber": "failed"}

    return SyncResult(jobber_expense_id=jobber_expense_id, sync_status=sync_status)
