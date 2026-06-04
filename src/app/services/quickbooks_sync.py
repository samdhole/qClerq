from __future__ import annotations

import asyncio
from typing import Any

from app.schemas.invoice import SyncRequest, SyncResult

# Stable identity tag written into the Bill PrivateNote so a retry can find the
# already-created Bill instead of creating a duplicate (H-1 idempotency).
_HASH_TAG_PREFIX = "qclerq-file-hash:"


def _hash_tag(file_hash: str) -> str:
    return f"{_HASH_TAG_PREFIX}{file_hash}"


def _find_existing_bill_id(req: SyncRequest, qb: Any) -> str | None:
    """Return the Id of a Bill already created for this invoice, or None.

    Idempotency guard (H-1): we tag every Bill with the invoice's file_hash in
    PrivateNote and set DocNumber to invoice_number. QBO SQL can filter on the
    queryable DocNumber, so we query by it and then confirm the PrivateNote tag
    to avoid cross-vendor invoice_number collisions. When invoice_number is
    absent there is no queryable key, so we skip the check and let the create
    proceed (caller keeps this behind try/except so a query failure never blocks).
    """
    from quickbooks.objects.bill import Bill

    inv = req.invoice
    invoice_number = inv.invoice_number
    if not invoice_number:
        return None

    tag = _hash_tag(inv.file_hash)
    safe_doc = invoice_number.replace("'", "''")
    existing = Bill.where(f"DocNumber = '{safe_doc}'", qb=qb)
    for bill in existing or []:
        if getattr(bill, "PrivateNote", "") == tag:
            return str(bill.Id)
    return None


def _create_bill_sync(req: SyncRequest, settings: Any, account_id: str) -> tuple[str, bool]:
    """Create QB Bill (or return an existing one for a retry).

    Returns (qb_bill_id, created) where created is False when an existing Bill
    tagged with this invoice's file_hash was found (idempotent skip). Raises on
    create failure.
    """
    from quickbooks import QuickBooks
    from quickbooks.objects.base import Ref
    from quickbooks.objects.bill import Bill
    from quickbooks.objects.detailline import AccountBasedExpenseLine, AccountBasedExpenseLineDetail
    from quickbooks.objects.vendor import Vendor
    from intuitlib.client import AuthClient

    qb_env = getattr(settings, "qb_environment", "sandbox")
    auth_client = AuthClient(
        client_id=settings.qb_client_id,
        client_secret=settings.qb_client_secret,
        redirect_uri="https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl",
        environment=qb_env,
    )
    qb = QuickBooks(
        auth_client=auth_client,
        refresh_token=settings.qb_refresh_token,
        company_id=settings.qb_realm_id,
        minorversion=75,
    )

    # Idempotency check-before-create: if a Bill tagged with this file_hash already
    # exists, return it instead of creating a duplicate (H-1). Best-effort — a query
    # failure must NOT block the create, so we swallow it and fall through to create.
    try:
        existing_id = _find_existing_bill_id(req, qb)
    except Exception:
        existing_id = None
    if existing_id is not None:
        return existing_id, False

    # Look up vendor by normalized name — escape single quotes to prevent QBOSQL injection (C-3)
    vendor_name = req.invoice.vendor_normalized
    safe_name = vendor_name.replace("'", "''")
    vendors = Vendor.where(f"DisplayName = '{safe_name}'", qb=qb)
    if not vendors:
        raise ValueError(f"Vendor '{vendor_name}' not found in QuickBooks")

    vendor_ref = Ref()
    vendor_ref.value = vendors[0].Id
    vendor_ref.name = vendor_name

    lines = []
    for item in req.invoice.line_items:
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = Ref()
        detail.AccountRef.value = account_id

        line = AccountBasedExpenseLine()
        line.Amount = item.line_total
        line.DetailType = "AccountBasedExpenseLineDetail"
        line.AccountBasedExpenseLineDetail = detail
        line.Description = item.description
        lines.append(line)

    if not lines:
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = Ref()
        detail.AccountRef.value = account_id
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
    # Tag the Bill with the invoice identity so retries dedupe (H-1).
    if req.invoice.invoice_number:
        bill.DocNumber = req.invoice.invoice_number
    bill.PrivateNote = _hash_tag(req.invoice.file_hash)

    bill.save(qb=qb)
    return str(bill.Id), True


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Create QuickBooks Bill. Returns SyncResult with qb_bill_id.

    Idempotent (H-1): a retry of an already-synced invoice returns the existing
    qb_bill_id with sync_status "skipped" instead of creating a second Bill.
    """
    try:
        qb_bill_id, created = await asyncio.to_thread(
            _create_bill_sync, req, settings, settings.qb_default_expense_account_id
        )
        sync_status = {"quickbooks": "ok" if created else "skipped"}
    except Exception:
        qb_bill_id = None
        sync_status = {"quickbooks": "failed"}

    return SyncResult(qb_bill_id=qb_bill_id, sync_status=sync_status)
