from __future__ import annotations

import pytest
import os
import tempfile
from datetime import date
from pydantic import ValidationError

from app.schemas.invoice import (
    InvoiceExtracted,
    LineItem,
    ExceptionItem,
    ValidationResult,
    SyncResult,
    WeeklySummary,
)
from app.config import Settings


def make_line_item(**overrides) -> dict:
    """Factory for LineItem test data."""
    base = {
        "description": "Widget",
        "quantity": 2.0,
        "unit_price": 50.0,
        "line_total": 100.0,
        "category": "materials",
    }
    return {**base, **overrides}


def make_invoice(**overrides) -> dict:
    """Factory for InvoiceExtracted test data."""
    base = {
        "vendor_raw": "Acme Corp LLC",
        "vendor_normalized": "Acme Corp",
        "subtotal": 100.0,
        "tax": 8.0,
        "shipping": 0.0,
        "discount": 0.0,
        "total": 108.0,
        "line_items": [make_line_item()],
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "abc123",
        "file_name": "invoice.pdf",
    }
    return {**base, **overrides}


# LineItem tests
def test_line_item_round_trip():
    """LineItem round-trips correctly: construct → model_dump → reconstruct."""
    data = make_line_item()
    item = LineItem(**data)
    dumped = item.model_dump()
    reconstructed = LineItem(**dumped)
    assert reconstructed == item


def test_line_item_accepts_all_categories():
    """LineItem.category accepts all valid literals."""
    categories = ["materials", "labor", "software", "utilities", "rent", "unknown"]
    for cat in categories:
        item = LineItem(**make_line_item(category=cat))
        assert item.category == cat


def test_line_item_rejects_invalid_category():
    """LineItem.category rejects invalid literals."""
    with pytest.raises(ValidationError):
        LineItem(**make_line_item(category="invalid"))


def test_line_item_required_fields():
    """LineItem enforces required fields."""
    required = ["description", "quantity", "unit_price", "line_total", "category"]
    data = make_line_item()
    for field in required:
        partial_data = {k: v for k, v in data.items() if k != field}
        with pytest.raises(ValidationError):
            LineItem(**partial_data)


# InvoiceExtracted tests
def test_invoice_round_trip():
    """InvoiceExtracted round-trips correctly."""
    data = make_invoice()
    invoice = InvoiceExtracted(**data)
    dumped = invoice.model_dump()
    reconstructed = InvoiceExtracted(**dumped)
    assert reconstructed == invoice


def test_invoice_required_fields_enforced():
    """InvoiceExtracted enforces all required fields."""
    required = [
        "vendor_raw",
        "vendor_normalized",
        "subtotal",
        "tax",
        "shipping",
        "discount",
        "total",
        "file_hash",
        "file_name",
        "confidence_overall",
        "duplicate_risk",
        "line_items",
        "missing_required_fields",
        "warnings",
    ]
    data = make_invoice()
    for field in required:
        partial_data = {k: v for k, v in data.items() if k != field}
        with pytest.raises(ValidationError):
            InvoiceExtracted(**partial_data)


def test_invoice_optional_fields_accept_none():
    """Optional fields accept None."""
    optional = ["invoice_number", "invoice_date", "due_date", "po_number", "job_id", "payment_terms"]
    data = make_invoice()
    for field in optional:
        data[field] = None
        invoice = InvoiceExtracted(**data)
        assert getattr(invoice, field) is None


def test_invoice_duplicate_risk_literals():
    """duplicate_risk only accepts valid literals."""
    for risk in ["none", "possible", "likely"]:
        invoice = InvoiceExtracted(**make_invoice(duplicate_risk=risk))
        assert invoice.duplicate_risk == risk

    with pytest.raises(ValidationError):
        InvoiceExtracted(**make_invoice(duplicate_risk="invalid"))


def test_invoice_with_dates():
    """InvoiceExtracted accepts date objects."""
    data = make_invoice(
        invoice_date=date(2026, 5, 13),
        due_date=date(2026, 6, 13),
    )
    invoice = InvoiceExtracted(**data)
    assert invoice.invoice_date == date(2026, 5, 13)
    assert invoice.due_date == date(2026, 6, 13)


def test_invoice_currency_default():
    """currency defaults to 'USD'."""
    data = make_invoice()
    # Remove currency if present to test default
    data.pop("currency", None)
    invoice = InvoiceExtracted(**data)
    assert invoice.currency == "USD"


# ExceptionItem tests
def test_exception_item_round_trip():
    """ExceptionItem round-trips correctly."""
    data = {
        "type": "missing_invoice_number",
        "severity": "high",
        "message": "Invoice number is required but missing",
    }
    item = ExceptionItem(**data)
    dumped = item.model_dump()
    reconstructed = ExceptionItem(**dumped)
    assert reconstructed == item


def test_exception_item_severity_literals():
    """severity only accepts valid literals."""
    for severity in ["low", "medium", "high"]:
        item = ExceptionItem(
            type="test",
            severity=severity,
            message="test",
        )
        assert item.severity == severity

    with pytest.raises(ValidationError):
        ExceptionItem(type="test", severity="invalid", message="test")


# ValidationResult tests
def test_validation_result_round_trip():
    """ValidationResult round-trips correctly."""
    data = {
        "is_clean": True,
        "approval_tier": "auto",
        "exceptions": [
            {
                "type": "duplicate",
                "severity": "medium",
                "message": "Possible duplicate detected",
            }
        ],
    }
    result = ValidationResult(**data)
    dumped = result.model_dump()
    reconstructed = ValidationResult(**dumped)
    assert reconstructed == result


def test_validation_result_approval_tier_literals():
    """approval_tier only accepts valid literals."""
    for tier in ["auto", "manager", "cfo"]:
        result = ValidationResult(
            is_clean=True,
            approval_tier=tier,
            exceptions=[],
        )
        assert result.approval_tier == tier

    with pytest.raises(ValidationError):
        ValidationResult(
            is_clean=True,
            approval_tier="invalid",
            exceptions=[],
        )


# SyncResult tests
def test_sync_result_round_trip():
    """SyncResult round-trips correctly."""
    data = {
        "sheets_row_id": "row-123",
        "qb_bill_id": "bill-456",
        "jobber_expense_id": "exp-789",
        "sync_status": {
            "sheets": "ok",
            "quickbooks": "failed",
            "jobber": "skipped",
        },
    }
    result = SyncResult(**data)
    dumped = result.model_dump()
    reconstructed = SyncResult(**dumped)
    assert reconstructed == result


def test_sync_result_optional_ids():
    """SyncResult IDs are optional."""
    data = {
        "sheets_row_id": None,
        "qb_bill_id": None,
        "jobber_expense_id": None,
        "sync_status": {"sheets": "ok"},
    }
    result = SyncResult(**data)
    assert result.sheets_row_id is None
    assert result.qb_bill_id is None
    assert result.jobber_expense_id is None


def test_sync_result_status_values():
    """sync_status values are constrained to 'ok', 'failed', 'skipped'."""
    result = SyncResult(
        sync_status={
            "sheets": "ok",
            "quickbooks": "failed",
            "jobber": "skipped",
        }
    )
    assert result.sync_status["sheets"] == "ok"
    assert result.sync_status["quickbooks"] == "failed"
    assert result.sync_status["jobber"] == "skipped"

    with pytest.raises(ValidationError):
        SyncResult(sync_status={"sheets": "invalid"})


# WeeklySummary tests
def test_weekly_summary_round_trip():
    """WeeklySummary round-trips with all fields populated."""
    data = {
        "period_start": date(2026, 5, 6),
        "period_end": date(2026, 5, 12),
        "invoice_count": 42,
        "total_value": 15000.50,
        "exception_rate": 0.15,
        "sync_failures": {
            "sheets": 2,
            "quickbooks": 1,
            "jobber": 0,
        },
        "top_vendors": ["Acme Corp", "Widget Inc", "Services LLC"],
    }
    summary = WeeklySummary(**data)
    dumped = summary.model_dump()
    reconstructed = WeeklySummary(**dumped)
    assert reconstructed == summary
    assert reconstructed.period_start == date(2026, 5, 6)
    assert reconstructed.period_end == date(2026, 5, 12)
    assert reconstructed.invoice_count == 42
    assert abs(reconstructed.total_value - 15000.50) < 0.01
    assert abs(reconstructed.exception_rate - 0.15) < 0.01
    assert len(reconstructed.top_vendors) == 3


# Settings tests
def test_settings_loads_from_env_test():
    """Settings loads from .env.test correctly."""
    settings = Settings(_env_file=".env.test")
    assert settings.manager_email == "manager@test.local"
    assert settings.cfo_email == "cfo@test.local"
    assert settings.sheet_id == "test-sheet-id"
    assert settings.approval_tier_1_max == 500.0
    assert settings.approval_tier_2_max == 5000.0
    assert settings.confidence_threshold == 0.70


def test_settings_defaults():
    """Settings applies defaults for optional fields."""
    settings = Settings(_env_file=".env.test")
    assert settings.approval_tier_1_max == 500.0
    assert settings.llama_cloud_api_key == "test-llama-key"
    assert settings.google_service_account_json == "{}"


def test_settings_required_fields():
    """Settings enforces required fields."""
    # Test that Settings loaded from .env.test has required fields set
    settings = Settings(_env_file=".env.test")
    assert settings.manager_email is not None
    assert settings.cfo_email is not None
    assert settings.sheet_id is not None
    assert settings.anthropic_api_key is not None


def test_settings_missing_required_field_raises_error():
    """Settings raises ValidationError when required manager_email is missing."""
    # Create a temp .env file without manager_email
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("CFO_EMAIL=test@test.local\n")
        f.write("SHEET_ID=test-sheet\n")
        f.write("ANTHROPIC_API_KEY=test-key\n")
        temp_env = f.name

    try:
        with pytest.raises(ValidationError):
            Settings(_env_file=temp_env)
    finally:
        os.unlink(temp_env)
