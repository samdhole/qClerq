from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import gspread

from app.schemas.invoice import ExceptionItem, InvoiceExtracted, SyncRequest, SyncResult

_INVOICES_SHEET = "Invoices"
_EXCEPTIONS_SHEET = "Exceptions"

INVOICE_COLUMNS = [
    "file_hash", "file_name", "invoice_number", "invoice_date", "due_date",
    "vendor_raw", "vendor_normalized", "po_number", "job_id",
    "subtotal", "tax", "shipping", "discount", "total", "currency",
    "line_items", "payment_terms", "confidence_overall", "duplicate_risk",
    "missing_required_fields", "warnings",
    "approval_tier", "approval_status", "approved_by", "approval_notes", "approved_at",
    "sync_status", "qb_bill_id", "jobber_expense_id",
]

EXCEPTION_COLUMNS = [
    "file_hash", "file_name", "vendor_normalized", "invoice_number",
    "issue_type", "severity", "message", "status", "created_at",
]


def _get_client(service_account_json: str) -> gspread.Client:
    info = json.loads(service_account_json)
    return gspread.service_account_from_dict(info)


def _inv_to_row(req: SyncRequest, qb_bill_id: str | None, jobber_expense_id: str | None, sync_status: dict) -> list[Any]:
    inv = req.invoice
    return [
        inv.file_hash,
        inv.file_name,
        inv.invoice_number or "",
        str(inv.invoice_date) if inv.invoice_date else "",
        str(inv.due_date) if inv.due_date else "",
        inv.vendor_raw,
        inv.vendor_normalized,
        inv.po_number or "",
        inv.job_id or "",
        inv.subtotal,
        inv.tax,
        inv.shipping,
        inv.discount,
        inv.total,
        inv.currency,
        json.dumps([item.model_dump() for item in inv.line_items]),
        inv.payment_terms or "",
        inv.confidence_overall,
        inv.duplicate_risk,
        json.dumps(inv.missing_required_fields),
        json.dumps(inv.warnings),
        req.approval_tier,
        "approved",
        req.approved_by,
        req.approval_notes,
        req.approved_at.isoformat(),
        json.dumps(sync_status),
        qb_bill_id or "",
        jobber_expense_id or "",
    ]


def write_invoice_row(
    sheet_id: str,
    req: SyncRequest,
    qb_bill_id: str | None,
    jobber_expense_id: str | None,
    sync_status: dict,
    service_account_json: str,
) -> str:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    row = _inv_to_row(req, qb_bill_id, jobber_expense_id, sync_status)
    assert len(row) == len(INVOICE_COLUMNS), f"Row has {len(row)} values but INVOICE_COLUMNS has {len(INVOICE_COLUMNS)}"
    ws.append_row(row, value_input_option="USER_ENTERED")
    all_rows = ws.get_all_values()
    row_index = len(all_rows)
    return str(row_index)


def update_sync_status(sheet_id: str, row_index: int, sync_status: dict, qb_bill_id: str | None, jobber_expense_id: str | None, service_account_json: str) -> None:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    sync_col = INVOICE_COLUMNS.index("sync_status") + 1
    qb_col = INVOICE_COLUMNS.index("qb_bill_id") + 1
    jobber_col = INVOICE_COLUMNS.index("jobber_expense_id") + 1
    ws.update_cell(row_index, sync_col, json.dumps(sync_status))
    if qb_bill_id:
        ws.update_cell(row_index, qb_col, qb_bill_id)
    if jobber_expense_id:
        ws.update_cell(row_index, jobber_col, jobber_expense_id)


def write_exceptions(sheet_id: str, inv: InvoiceExtracted, exceptions: list[ExceptionItem], service_account_json: str) -> None:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_EXCEPTIONS_SHEET)
    now = datetime.now(timezone.utc).isoformat()
    for exc in exceptions:
        row = [
            inv.file_hash,
            inv.file_name,
            inv.vendor_normalized,
            inv.invoice_number or "",
            exc.type,
            exc.severity,
            exc.message,
            "open",
            now,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")


def get_known_hashes(sheet_id: str, service_account_json: str) -> set[str]:
    """Return all file_hash values from the Invoices tab for duplicate detection."""
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    hash_col = INVOICE_COLUMNS.index("file_hash") + 1
    values = ws.col_values(hash_col)
    return {v for v in values[1:] if v}  # skip header row


async def sync(req: SyncRequest, settings: Any) -> SyncResult:
    """Write invoice row to Sheets Invoices tab. Returns SyncResult with sheets_row_id.

    This is the public entry point for standalone Sheets sync (mainly for testing).
    For full multi-target sync, use api.sync_all which orchestrates Sheets + QB + Jobber.
    """
    sync_status: dict[str, str] = {}
    row_id: str | None = None
    try:
        row_id = await asyncio.to_thread(
            write_invoice_row,
            settings.sheet_id,
            req,
            None,
            None,
            sync_status,
            settings.google_service_account_json,
        )
        sync_status["sheets"] = "ok"
    except Exception:
        sync_status["sheets"] = "failed"
        row_id = None

    return SyncResult(
        sheets_row_id=row_id,
        sync_status=sync_status,
    )
