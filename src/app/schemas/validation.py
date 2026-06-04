from __future__ import annotations

REQUIRED_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "vendor_raw",
    "total",
    "invoice_date",
)

CONFIDENCE_FLOOR: float = 0.70

# Math-check tolerance: an internal arithmetic discrepancy is allowed up to
# the LARGER of MATH_TOLERANCE_ABS (absolute floor) or amount * MATH_TOLERANCE_REL
# (relative). The "absolute floor OR relative, whichever larger" shape is the
# documented contract; only the relative rate was tightened.
#
# MATH_TOLERANCE_REL was 0.02 (2%) — under M-1 (adversarial review 2026-06-04)
# that flat 2% let a $10k invoice be internally inconsistent by ~$200 and still
# validate clean, on exactly the high-value invoices a reviewer most trusts.
# Tightened to 0.005 (0.5%) so large invoices are actually checked (window on a
# $10k invoice drops from $200 to $50) while small invoices keep the $0.02 floor
# for rounding noise. Applied uniformly to all three math checks (total,
# line-items-vs-subtotal, per-line) via these constants.
MATH_TOLERANCE_ABS: float = 0.02

MATH_TOLERANCE_REL: float = 0.005
