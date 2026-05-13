"""Weekly report generator reading from Sheets Invoices + Exceptions tabs."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import date, timedelta
from typing import Any

import gspread

from app.schemas.invoice import WeeklySummary

logger = logging.getLogger(__name__)

# Sync targets for failure tracking
_SYNC_TARGETS = ("sheets", "quickbooks", "jobber")


def _get_sheets_client(service_account_json: str) -> gspread.Client:
    """Get authenticated gspread client from service account JSON."""
    info = json.loads(service_account_json)
    return gspread.service_account_from_dict(info)


def _parse_date(val: str) -> date | None:
    """Parse ISO date string safely."""
    try:
        return date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _generate_sync(
    sheet_id: str,
    period_start: date,
    period_end: date,
    service_account_json: str,
) -> WeeklySummary:
    """Generate weekly summary from Sheets (sync version).

    Args:
        sheet_id: Google Sheets ID
        period_start: Report period start date (inclusive)
        period_end: Report period end date (inclusive)
        service_account_json: Service account JSON string

    Returns:
        WeeklySummary with aggregated metrics for the period
    """
    gc = _get_sheets_client(service_account_json)
    sh = gc.open_by_key(sheet_id)

    # Read Invoices tab
    inv_ws = sh.worksheet("Invoices")
    all_rows = inv_ws.get_all_records()

    # Filter to period
    period_rows = [
        r
        for r in all_rows
        if (d := _parse_date(str(r.get("invoice_date", "")))) and period_start <= d <= period_end
    ]

    invoice_count = len(period_rows)
    total_value = sum(float(r.get("total", 0) or 0) for r in period_rows)

    # Sync failures per target
    sync_failures: dict[str, int] = {t: 0 for t in _SYNC_TARGETS}
    for r in period_rows:
        raw = r.get("sync_status", "{}")
        try:
            status = json.loads(str(raw)) if raw else {}
        except json.JSONDecodeError:
            status = {}
        for target in sync_failures:
            if status.get(target) == "failed":
                sync_failures[target] += 1

    # Top vendors
    vendor_counts: Counter[str] = Counter(
        str(r.get("vendor_normalized", "")) for r in period_rows if r.get("vendor_normalized")
    )
    top_vendors = [vendor for vendor, _ in vendor_counts.most_common(5)]

    # Exception rate from Exceptions tab
    try:
        exc_ws = sh.worksheet("Exceptions")
        exc_rows = exc_ws.get_all_records()
        exc_in_period = [
            r
            for r in exc_rows
            if (d := _parse_date(str(r.get("created_at", ""))[:10]))
            and period_start <= d <= period_end
        ]
        exception_rate = len(exc_in_period) / max(invoice_count, 1)
    except gspread.exceptions.WorksheetNotFound:
        exception_rate = 0.0
    except Exception:
        logger.warning("Exceptions tab read failed", exc_info=True)
        exception_rate = 0.0

    return WeeklySummary(
        period_start=period_start,
        period_end=period_end,
        invoice_count=invoice_count,
        total_value=total_value,
        exception_rate=exception_rate,
        sync_failures=sync_failures,
        top_vendors=top_vendors,
    )


async def generate(settings: Any) -> WeeklySummary:
    """Generate weekly summary for the past 7 days.

    Args:
        settings: Application settings with sheet_id and google_service_account_json

    Returns:
        WeeklySummary for the past 7 days (excluding today)
    """
    today = date.today()
    period_start = today - timedelta(days=7)
    period_end = today - timedelta(days=1)

    return await asyncio.to_thread(
        _generate_sync,
        settings.sheet_id,
        period_start,
        period_end,
        settings.google_service_account_json,
    )
