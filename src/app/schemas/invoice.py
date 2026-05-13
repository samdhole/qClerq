from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float
    category: Literal["materials", "labor", "software", "utilities", "rent", "unknown"]


class InvoiceExtracted(BaseModel):
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    vendor_raw: str
    vendor_normalized: str
    po_number: str | None = None
    job_id: str | None = None
    subtotal: float
    tax: float
    shipping: float
    discount: float
    total: float
    currency: str = "USD"
    line_items: list[LineItem]
    payment_terms: str | None = None
    confidence_overall: float
    duplicate_risk: Literal["none", "possible", "likely"]
    missing_required_fields: list[str]
    warnings: list[str]
    file_hash: str
    file_name: str


class ExceptionItem(BaseModel):
    type: str
    severity: Literal["low", "medium", "high"]
    message: str


class ValidationResult(BaseModel):
    is_clean: bool
    approval_tier: Literal["auto", "manager", "cfo"]
    exceptions: list[ExceptionItem]


class SyncResult(BaseModel):
    sheets_row_id: str | None = None
    qb_bill_id: str | None = None
    jobber_expense_id: str | None = None
    sync_status: dict[str, Literal["ok", "failed", "skipped"]]


class WeeklySummary(BaseModel):
    period_start: date
    period_end: date
    invoice_count: int
    total_value: float
    exception_rate: float
    sync_failures: dict[str, int]
    top_vendors: list[str]


class SyncRequest(BaseModel):
    invoice: InvoiceExtracted
    approved_by: str
    approval_notes: str = ""
    approved_at: datetime
    approval_tier: Literal["auto", "manager", "cfo"]
