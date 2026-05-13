from __future__ import annotations

REQUIRED_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "vendor_raw",
    "total",
    "invoice_date",
)

CONFIDENCE_FLOOR: float = 0.70

MATH_TOLERANCE_ABS: float = 0.02

MATH_TOLERANCE_REL: float = 0.02
