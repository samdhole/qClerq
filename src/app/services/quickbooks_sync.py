from __future__ import annotations

import asyncio
from typing import Any

from app.schemas.invoice import SyncRequest, SyncResult


def _create_bill_sync(req: SyncRequest, settings: Any) -> str:
    """Create QB Bill and return qb_bill_id. Raises on failure."""
    from quickbooks import QuickBooks
    from quickbooks.objects.base import Ref
    from quickbooks.objects.bill import Bill
    from quickbooks.objects.detailline import AccountBasedExpenseLine, AccountBasedExpenseLineDetail
    from quickbooks.objects.vendor import Vendor
    from intuitlib.client import AuthClient

    auth_client = AuthClient(
        client_id=settings.qb_client_id,
        client_secret=settings.qb_client_secret,
        redirect_uri="https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl",
        environment="production",
    )
    qb = QuickBooks(
        auth_client=auth_client,
        refresh_token=settings.qb_refresh_token,
        company_id=settings.qb_realm_id,
        minorversion=75,
    )

    # Look up vendor by normalized name
    vendor_name = req.invoice.vendor_normalized
    vendors = Vendor.where(f"DisplayName = '{vendor_name}'", qb=qb)
    if not vendors:
        raise ValueError(f"Vendor '{vendor_name}' not found in QuickBooks")

    vendor_ref = Ref()
    vendor_ref.value = vendors[0].Id
    vendor_ref.name = vendor_name

    lines = []
    for item in req.invoice.line_items:
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = Ref()
        detail.AccountRef.value = "1"

        line = AccountBasedExpenseLine()
        line.Amount = item.line_total
        line.DetailType = "AccountBasedExpenseLineDetail"
        line.AccountBasedExpenseLineDetail = detail
        line.Description = item.description
        lines.append(line)

    if not lines:
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = Ref()
        detail.AccountRef.value = "1"
        line = AccountBasedExpenseLine()
        line.Amount = req.invoice.total
        line.DetailType = "AccountBasedExpenseLineDetail"
        line.AccountBasedExpenseLineDetail = detail
        line.Description = f"Invoice {req.invoice.invoice_number or 'unknown'}"
        lines = [line]

    bill = Bill()
    bill.VendorRef = vendor_ref
    bill.Line = lines
    if req.invoice.invoice_date:
        bill.TxnDate = str(req.invoice.invoice_date)

    bill.save(qb=qb)
    return str(bill.Id)


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Create QuickBooks Bill. Returns SyncResult with qb_bill_id."""
    try:
        qb_bill_id = await asyncio.to_thread(_create_bill_sync, req, settings)
        sync_status = {"quickbooks": "ok"}
    except Exception:
        qb_bill_id = None
        sync_status = {"quickbooks": "failed"}

    return SyncResult(qb_bill_id=qb_bill_id, sync_status=sync_status)
