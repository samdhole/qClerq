from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal, NamedTuple


def compute_hash(pdf_bytes: bytes) -> str:
    """Compute SHA-256 hex digest of PDF bytes."""
    return hashlib.sha256(pdf_bytes).hexdigest()


def check_duplicate(
    file_hash: str,
    known_hashes: set[str],
) -> Literal["none", "possible", "likely"]:
    """Check file_hash against known_hashes set.

    Returns:
        "likely"   — hash is an exact match (same file submitted before)
        "none"     — hash not found in known_hashes
    """
    if file_hash in known_hashes:
        return "likely"
    return "none"


class KnownInvoice(NamedTuple):
    vendor_normalized: str
    invoice_number: str
    total: float
    invoice_date: date | None


def check_semantic_duplicate(
    vendor_normalized: str,
    invoice_number: str | None,
    total: float,
    invoice_date: date | None,
    known_invoices: list[KnownInvoice],
) -> Literal["none", "possible", "likely"]:
    """Check for semantic duplicates using vendor+invoice_number and vendor+total+date keys.

    Key 2: same (vendor_normalized, invoice_number) → "likely"
    Key 3: same (vendor_normalized, total, invoice_date ±3 days) → "possible"
    """
    norm_vendor = vendor_normalized.lower().strip()
    norm_inv_num = (invoice_number or "").strip().lower()

    for known in known_invoices:
        if known.vendor_normalized.lower().strip() != norm_vendor:
            continue

        if norm_inv_num and known.invoice_number.strip().lower() == norm_inv_num:
            return "likely"

        if known.invoice_date and invoice_date:
            date_close = abs((known.invoice_date - invoice_date).days) <= 3
            total_match = abs(known.total - total) < 0.01
            if date_close and total_match:
                return "possible"

    return "none"
