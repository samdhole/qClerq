from __future__ import annotations

from typing import Literal

from app.schemas.invoice import ExceptionItem, InvoiceExtracted, ValidationResult
from app.schemas.validation import (
    CONFIDENCE_FLOOR,
    MATH_TOLERANCE_ABS,
    MATH_TOLERANCE_REL,
    REQUIRED_FIELDS,
)


def validate(
    inv: InvoiceExtracted, approval_tier_1_max: float, approval_tier_2_max: float
) -> ValidationResult:
    """Run deterministic validation checks. Returns ValidationResult with all exceptions found.

    Pure function — no I/O, no LLM calls, no side effects.
    """
    exceptions: list[ExceptionItem] = []

    # Math check: subtotal + tax + shipping - discount == total (within tolerance)
    calculated = inv.subtotal + inv.tax + inv.shipping - inv.discount
    tolerance = max(MATH_TOLERANCE_ABS, abs(inv.total) * MATH_TOLERANCE_REL)
    if abs(calculated - inv.total) > tolerance:
        exceptions.append(
            ExceptionItem(
                type="math_error",
                severity="high",
                message=(
                    f"Math mismatch: {inv.subtotal} + {inv.tax} + {inv.shipping} - "
                    f"{inv.discount} = {calculated:.2f}, but total = {inv.total:.2f} "
                    f"(tolerance ±{tolerance:.2f})"
                ),
            )
        )

    # Missing required fields
    for field in REQUIRED_FIELDS:
        value = getattr(inv, field, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            exceptions.append(
                ExceptionItem(
                    type="missing_required_field",
                    severity="high",
                    message=f"Required field '{field}' is missing or empty",
                )
            )

    # Low confidence
    if inv.confidence_overall < CONFIDENCE_FLOOR:
        exceptions.append(
            ExceptionItem(
                type="low_confidence",
                severity="medium",
                message=(
                    f"confidence_overall {inv.confidence_overall:.2f} is below "
                    f"threshold {CONFIDENCE_FLOOR}"
                ),
            )
        )

    # Duplicate risk
    if inv.duplicate_risk in ("possible", "likely"):
        severity: Literal["low", "medium", "high"] = (
            "high" if inv.duplicate_risk == "likely" else "medium"
        )
        exceptions.append(
            ExceptionItem(
                type="duplicate_risk",
                severity=severity,
                message=f"Invoice flagged as duplicate_risk='{inv.duplicate_risk}'",
            )
        )

    # Determine approval tier
    if inv.total < approval_tier_1_max:
        tier: Literal["auto", "manager", "cfo"] = "auto"
    elif inv.total <= approval_tier_2_max:
        tier = "manager"
    else:
        tier = "cfo"

    is_clean = len(exceptions) == 0
    return ValidationResult(
        is_clean=is_clean,
        approval_tier=tier,
        exceptions=exceptions,
    )
