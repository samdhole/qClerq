from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# H-4: Constrained numeric types used throughout financial models.
# - allow_inf_nan=False on both models blocks NaN/Infinity from construction.
# - Field constraints make negative/zero values structurally impossible rather than
#   a validation exception discovered deep in execution (defense-in-depth).
PositiveAmount = Annotated[float, Field(gt=0)]
NonNegativeAmount = Annotated[float, Field(ge=0)]
PositiveQuantity = Annotated[float, Field(gt=0)]


class LineItem(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    description: str
    quantity: PositiveQuantity
    unit_price: NonNegativeAmount
    line_total: NonNegativeAmount
    category: Literal["materials", "labor", "software", "utilities", "rent", "unknown"]


class InvoiceExtracted(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    vendor_raw: str
    vendor_normalized: str
    po_number: str | None = None
    job_id: str | None = None
    subtotal: NonNegativeAmount
    tax: NonNegativeAmount
    shipping: NonNegativeAmount
    discount: NonNegativeAmount
    total: PositiveAmount
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
