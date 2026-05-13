from __future__ import annotations

from typing import Literal

from app.schemas.invoice import ExceptionItem, InvoiceExtracted, ValidationResult
from app.schemas.validation import (
    CONFIDENCE_FLOOR,
    MATH_TOLERANCE_ABS,
    MATH_TOLERANCE_REL,
    REQUIRED_FIELDS,
)
from app.services.approval_router import route as compute_tier


def validate(
    inv: InvoiceExtracted,
    approval_tier_1_max: float,
    approval_tier_2_max: float,
    dedupe_result: Literal["none", "possible", "likely"] = "none",
) -> ValidationResult:
    """Run deterministic validation checks. Returns ValidationResult with all exceptions found.

    Pure function — no I/O, no LLM calls, no side effects.

    dedupe_result must be passed explicitly by the caller (from dedupe.check_duplicate /
    check_semantic_duplicate) rather than read from inv.duplicate_risk, which is LLM-supplied.
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

    # Line-items sum vs subtotal (H-7)
    if inv.line_items:
        items_sum = sum(item.line_total for item in inv.line_items)
        if abs(items_sum - inv.subtotal) > tolerance:
            exceptions.append(
                ExceptionItem(
                    type="line_items_subtotal_mismatch",
                    severity="high",
                    message=(
                        f"Line items sum {items_sum:.2f} does not match "
                        f"subtotal {inv.subtotal:.2f} (tolerance ±{tolerance:.2f})"
                    ),
                )
            )

        # Per-line quantity × unit_price ≈ line_total (H-8)
        for i, item in enumerate(inv.line_items):
            expected = item.quantity * item.unit_price
            line_tol = max(MATH_TOLERANCE_ABS, abs(item.line_total) * MATH_TOLERANCE_REL)
            if abs(expected - item.line_total) > line_tol:
                exceptions.append(
                    ExceptionItem(
                        type="line_item_math_error",
                        severity="medium",
                        message=(
                            f"Line item {i + 1}: {item.quantity} × {item.unit_price} = "
                            f"{expected:.2f}, but line_total = {item.line_total:.2f} "
                            f"(tolerance ±{line_tol:.2f})"
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

    # Low confidence — informational only, not a security gate (M-1)
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

    # Duplicate risk from deterministic dedupe result (not LLM self-report) (M-1)
    if dedupe_result in ("possible", "likely"):
        severity: Literal["low", "medium", "high"] = (
            "high" if dedupe_result == "likely" else "medium"
        )
        exceptions.append(
            ExceptionItem(
                type="duplicate_risk",
                severity=severity,
                message=f"Invoice flagged as duplicate_risk='{dedupe_result}'",
            )
        )

    # Determine approval tier
    tier: Literal["auto", "manager", "cfo"] = compute_tier(
        inv.total, approval_tier_1_max, approval_tier_2_max
    )

    is_clean = len(exceptions) == 0
    return ValidationResult(
        is_clean=is_clean,
        approval_tier=tier,
        exceptions=exceptions,
    )
