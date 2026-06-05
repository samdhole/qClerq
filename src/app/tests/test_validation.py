from datetime import date

import pytest

from app.schemas.invoice import ExceptionItem, InvoiceExtracted, ValidationResult
from app.services.validator import validate

TIER1_MAX = 500.0
TIER2_MAX = 5000.0


def make_invoice(**overrides) -> InvoiceExtracted:
    """Helper to create a valid InvoiceExtracted with defaults."""
    defaults = {
        "vendor_raw": "Acme Corp LLC",
        "vendor_normalized": "Acme Corp",
        "subtotal": 100.0,
        "tax": 8.0,
        "shipping": 0.0,
        "discount": 0.0,
        "total": 108.0,
        "line_items": [],
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "abc123",
        "file_name": "invoice.pdf",
        "invoice_number": "INV-001",
        "invoice_date": date(2026, 1, 15),
    }
    return InvoiceExtracted.model_validate({**defaults, **overrides})


class TestMathCheck:
    """AC2.3 and AC2.4: Math check with tolerance."""

    def test_math_check_passes_when_balanced(self):
        """AC2.3: subtotal + tax + shipping - discount == total."""
        inv = make_invoice(
            subtotal=100.0,
            tax=8.0,
            shipping=0.0,
            discount=0.0,
            total=108.0,
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is True
        assert not any(e.type == "math_error" for e in result.exceptions)

    def test_math_error_when_totals_mismatch(self):
        """AC2.4: Math mismatch produces math_error exception."""
        inv = make_invoice(
            subtotal=100.0,
            tax=8.0,
            shipping=0.0,
            discount=0.0,
            total=150.0,  # should be 108.0
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert any(e.type == "math_error" for e in result.exceptions)

    def test_math_tolerance_absolute(self):
        """Math check respects $0.02 absolute tolerance."""
        # Total $1000, calculated $1000.01 (within $0.02 tolerance)
        inv = make_invoice(
            subtotal=900.0,
            tax=80.0,
            shipping=20.01,
            discount=0.0,
            total=1000.0,
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is True
        assert not any(e.type == "math_error" for e in result.exceptions)

    def test_math_tolerance_relative(self):
        """Math check respects 0.5% relative tolerance (M-1)."""
        # Total $1000, 0.5% = $5 tolerance
        # Calculated = $995.00, diff = $5.00, within tolerance
        inv = make_invoice(
            subtotal=915.0,
            tax=80.0,
            shipping=0.0,
            discount=0.0,
            total=1000.0,  # calculated = 995, diff = 5 = max(0.02, 1000 * 0.005)
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        # This is at the edge of tolerance, should pass
        # 995 vs 1000 = 5 difference, tolerance is max(0.02, 1000 * 0.005) = 5
        assert result.is_clean is True

    def test_tight_tolerance_flags_oversized_large_invoice(self):
        """M-1: a discrepancy in the old 0.5%-2% blind spot is now flagged.

        $10,000 invoice off by $100 (1%) passed under the old flat 2% window
        (tolerance was $200). Under the tightened 0.5% relative tolerance the
        window is $50, so this internal inconsistency is now caught.
        """
        inv = make_invoice(
            subtotal=9900.0,
            tax=0.0,
            shipping=0.0,
            discount=0.0,
            total=10000.0,  # calculated = 9900, diff = 100 > $50 tolerance
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert any(e.type == "math_error" for e in result.exceptions)

    def test_tight_tolerance_allows_close_large_invoice(self):
        """M-1: a legitimately-close large invoice still validates clean.

        $10,000 invoice off by $30 (0.3%) stays within the $50 (0.5%) window.
        """
        inv = make_invoice(
            subtotal=9970.0,
            tax=0.0,
            shipping=0.0,
            discount=0.0,
            total=10000.0,  # calculated = 9970, diff = 30 < $50 tolerance
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is True
        assert not any(e.type == "math_error" for e in result.exceptions)


class TestRequiredFields:
    """AC2.5: Missing required fields produce exceptions."""

    def test_missing_invoice_number(self):
        """AC2.5: Missing invoice_number produces exception."""
        inv = make_invoice(invoice_number=None)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert any(
            e.type == "missing_required_field" and "invoice_number" in e.message
            for e in result.exceptions
        )

    def test_missing_vendor_raw(self):
        """AC2.5: Missing vendor_raw produces exception."""
        inv = make_invoice(vendor_raw="")
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert any(
            e.type == "missing_required_field" and "vendor_raw" in e.message
            for e in result.exceptions
        )

    def test_zero_total_is_now_schema_error(self):
        """H-4: zero total is rejected by the schema before reaching the validator.

        Previously this test asserted "zero total is not treated as missing_required_field"
        because InvoiceExtracted accepted total=0.0. H-4 adds Field(gt=0), so total=0.0
        raises ValidationError at construction — the validator never sees it.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            make_invoice(total=0.0)

    def test_missing_invoice_date(self):
        """AC2.5: Missing invoice_date produces exception."""
        inv = make_invoice(invoice_date=None)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert any(
            e.type == "missing_required_field" and "invoice_date" in e.message
            for e in result.exceptions
        )

    def test_missing_all_required_fields(self):
        """AC2.5: Missing all 4 required fields produces 4 exceptions."""
        inv = make_invoice(
            invoice_number=None,
            vendor_raw="",
            invoice_date=None,
        )
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        missing_field_exceptions = [
            e for e in result.exceptions if e.type == "missing_required_field"
        ]
        # Should have 3 exceptions (invoice_number, vendor_raw, invoice_date)
        # total is always required by schema
        assert len(missing_field_exceptions) == 3


class TestConfidence:
    """AC2.6: Low confidence produces exception."""

    def test_low_confidence_below_floor(self):
        """AC2.6: confidence_overall < 0.70 produces low_confidence exception."""
        inv = make_invoice(confidence_overall=0.65)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert any(e.type == "low_confidence" for e in result.exceptions)

    def test_confidence_at_floor_is_clean(self):
        """AC2.6: confidence_overall >= 0.70 (at threshold) is clean."""
        inv = make_invoice(confidence_overall=0.70)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is True
        assert not any(e.type == "low_confidence" for e in result.exceptions)

    def test_high_confidence_is_clean(self):
        """High confidence is clean."""
        inv = make_invoice(confidence_overall=0.95)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is True
        assert not any(e.type == "low_confidence" for e in result.exceptions)


class TestApprovalTier:
    """Approval tier assignment based on total."""

    def test_auto_tier_for_small_invoice(self):
        """Invoices < tier1_max get auto approval."""
        inv = make_invoice(total=100.0)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.approval_tier == "auto"

    def test_manager_tier_for_medium_invoice(self):
        """Invoices tier1_max <= total <= tier2_max get manager approval."""
        inv = make_invoice(total=1000.0)  # between 500 and 5000
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.approval_tier == "manager"

    def test_cfo_tier_for_large_invoice(self):
        """Invoices > tier2_max get CFO approval."""
        inv = make_invoice(total=6000.0)  # > 5000
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.approval_tier == "cfo"

    def test_boundary_at_tier1_max(self):
        """Invoice exactly at tier1_max goes to manager tier."""
        inv = make_invoice(total=500.0)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.approval_tier == "manager"

    def test_boundary_at_tier2_max(self):
        """Invoice exactly at tier2_max goes to manager tier."""
        inv = make_invoice(total=5000.0)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.approval_tier == "manager"


class TestDuplicateRisk:
    """Duplicate risk flags in validation."""

    def test_duplicate_risk_none_is_clean(self):
        """duplicate_risk='none' does not produce exception."""
        inv = make_invoice()
        result = validate(inv, TIER1_MAX, TIER2_MAX, dedupe_result="none")
        assert not any(e.type == "duplicate_risk" for e in result.exceptions)

    def test_duplicate_risk_possible(self):
        """dedupe_result='possible' produces medium severity exception."""
        inv = make_invoice()
        result = validate(inv, TIER1_MAX, TIER2_MAX, dedupe_result="possible")
        assert result.is_clean is False
        dup_exceptions = [e for e in result.exceptions if e.type == "duplicate_risk"]
        assert len(dup_exceptions) == 1
        assert dup_exceptions[0].severity == "medium"

    def test_duplicate_risk_likely(self):
        """dedupe_result='likely' produces high severity exception."""
        inv = make_invoice()
        result = validate(inv, TIER1_MAX, TIER2_MAX, dedupe_result="likely")
        assert result.is_clean is False
        dup_exceptions = [e for e in result.exceptions if e.type == "duplicate_risk"]
        assert len(dup_exceptions) == 1
        assert dup_exceptions[0].severity == "high"


class TestValidationResult:
    """ValidationResult structure and properties."""

    def test_clean_invoice_has_empty_exceptions(self):
        """Clean invoice has is_clean=True and empty exceptions list."""
        inv = make_invoice()
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is True
        assert result.exceptions == []

    def test_is_clean_false_when_exceptions_present(self):
        """is_clean=False when any exceptions found."""
        inv = make_invoice(confidence_overall=0.50)
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert result.is_clean is False
        assert len(result.exceptions) > 0

    def test_result_is_validation_result_type(self):
        """Return value is ValidationResult type."""
        inv = make_invoice()
        result = validate(inv, TIER1_MAX, TIER2_MAX)
        assert isinstance(result, ValidationResult)
        assert hasattr(result, "is_clean")
        assert hasattr(result, "approval_tier")
        assert hasattr(result, "exceptions")
