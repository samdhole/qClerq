from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import gspread

from app.schemas.invoice import ExceptionItem, InvoiceExtracted, SyncRequest
from app.services.dedupe import KnownInvoice

logger = logging.getLogger(__name__)

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


def _inv_to_row(
    req: SyncRequest,
    qb_bill_id: str | None,
    jobber_expense_id: str | None,
    sync_status: dict,
    approval_status: str = "approved",
) -> list[Any]:
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
        approval_status,
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
    approval_status: str = "approved",
) -> str:
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    row = _inv_to_row(req, qb_bill_id, jobber_expense_id, sync_status, approval_status)
    if len(row) != len(INVOICE_COLUMNS):
        raise ValueError(
            f"Row has {len(row)} values but INVOICE_COLUMNS has {len(INVOICE_COLUMNS)}"
        )
    result = ws.append_row(row, value_input_option="USER_ENTERED")
    # Derive row index from the API response to avoid TOCTOU race (H-5)
    updated_range = result.get("updates", {}).get("updatedRange", "")
    m = re.search(r":?[A-Z]+(\d+)$", updated_range)
    if m:
        return m.group(1)
    # Fallback: count rows (best-effort, non-concurrent path only)
    return str(len(ws.get_all_values()))


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


def get_known_invoice_data(
    sheet_id: str,
    service_account_json: str,
) -> tuple[set[str], list[KnownInvoice]]:
    """Return known hashes and invoice tuples for duplicate detection (H-1).

    Returns:
        (known_hashes, known_invoices) — hashes for exact-file dedupe,
        invoice tuples for semantic dedupe by vendor+invoice_number and vendor+total+date.
    """
    gc = _get_client(service_account_json)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(_INVOICES_SHEET)
    all_rows = ws.get_all_records(expected_headers=INVOICE_COLUMNS)

    known_hashes: set[str] = set()
    known_invoices: list[KnownInvoice] = []

    hash_idx = INVOICE_COLUMNS.index("file_hash")
    vendor_idx = INVOICE_COLUMNS.index("vendor_normalized")
    inv_num_idx = INVOICE_COLUMNS.index("invoice_number")
    total_idx = INVOICE_COLUMNS.index("total")
    date_idx = INVOICE_COLUMNS.index("invoice_date")

    for row in all_rows:
        h = str(row.get(INVOICE_COLUMNS[hash_idx], "")).strip()
        if h:
            known_hashes.add(h)

        vendor = str(row.get(INVOICE_COLUMNS[vendor_idx], "")).strip()
        inv_num = str(row.get(INVOICE_COLUMNS[inv_num_idx], "")).strip()
        try:
            total = float(row.get(INVOICE_COLUMNS[total_idx], 0) or 0)
        except (ValueError, TypeError):
            total = 0.0
        raw_date = str(row.get(INVOICE_COLUMNS[date_idx], "")).strip()
        try:
            inv_date: date | None = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            inv_date = None

        if vendor:
            known_invoices.append(KnownInvoice(vendor, inv_num, total, inv_date))

    return known_hashes, known_invoices


# Keep old name as alias for any callers not yet updated
def get_known_hashes(sheet_id: str, service_account_json: str) -> set[str]:
    hashes, _ = get_known_invoice_data(sheet_id, service_account_json)
    return hashes
