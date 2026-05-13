from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.invoice import InvoiceExtracted, ValidationResult


def make_extracted(**overrides) -> dict:
    """Helper to create a valid InvoiceExtracted dict with defaults."""
    defaults = {
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-15",
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
        "file_name": "test.pdf",
    }
    return {**defaults, **overrides}


class TestHealth:
    """Test /health endpoint."""

    def test_health_returns_ok(self, client):
        """GET /health returns 200 with status=ok."""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestValidateEndpoint:
    """Test /validate endpoint for approval tier routing (AC3.1-3.3)."""

    def test_validate_auto_tier(self, client):
        """POST /validate with total < 500 returns approval_tier='auto' (AC3.1)."""
        r = client.post("/validate", json=make_extracted(total=100.0, subtotal=92.0, tax=8.0))
        assert r.status_code == 200
        data = r.json()
        assert data["approval_tier"] == "auto"
        assert data["is_clean"] is True

    def test_validate_manager_tier(self, client):
        """POST /validate with 500 <= total <= 5000 returns approval_tier='manager' (AC3.2)."""
        r = client.post("/validate", json=make_extracted(
            total=1000.0, subtotal=920.0, tax=80.0
        ))
        assert r.status_code == 200
        assert r.json()["approval_tier"] == "manager"

    def test_validate_cfo_tier(self, client):
        """POST /validate with total > 5000 returns approval_tier='cfo' (AC3.3)."""
        r = client.post("/validate", json=make_extracted(
            total=10000.0, subtotal=9000.0, tax=1000.0
        ))
        assert r.status_code == 200
        assert r.json()["approval_tier"] == "cfo"

    def test_validate_boundary_at_500(self, client):
        """POST /validate with total=500 (at tier1_max) returns 'manager'."""
        r = client.post("/validate", json=make_extracted(
            total=500.0, subtotal=460.0, tax=40.0
        ))
        assert r.status_code == 200
        assert r.json()["approval_tier"] == "manager"

    def test_validate_boundary_at_5000(self, client):
        """POST /validate with total=5000 (at tier2_max) returns 'manager'."""
        r = client.post("/validate", json=make_extracted(
            total=5000.0, subtotal=4600.0, tax=400.0
        ))
        assert r.status_code == 200
        assert r.json()["approval_tier"] == "manager"

    def test_validate_with_math_error(self, client):
        """POST /validate with math error returns is_clean=False with exceptions."""
        r = client.post("/validate", json=make_extracted(
            subtotal=100.0, tax=8.0, total=150.0  # mismatch
        ))
        assert r.status_code == 200
        data = r.json()
        assert data["is_clean"] is False
        assert any(e["type"] == "math_error" for e in data["exceptions"])

    def test_validate_returns_validation_result(self, client):
        """POST /validate returns ValidationResult with required fields."""
        r = client.post("/validate", json=make_extracted())
        assert r.status_code == 200
        data = r.json()
        assert "is_clean" in data
        assert "approval_tier" in data
        assert "exceptions" in data
        assert isinstance(data["exceptions"], list)


class TestApprovalCallbackEndpoint:
    """Test /approval-callback endpoint (AC3.4, AC3.5)."""

    def test_approval_callback_rejects_missing_approved_by(self, client):
        """POST /approval-callback without approved_by returns 422 (AC3.5)."""
        req = {
            "invoice": make_extracted(),
            "approved_by": "",  # empty/missing
            "approval_notes": "",
            "approved_at": "2026-05-13T10:00:00Z",
            "approval_tier": "auto",
        }
        r = client.post("/approval-callback", json=req)
        assert r.status_code == 422

    def test_approval_callback_with_approved_by_calls_sync(self, client):
        """POST /approval-callback with approved_by calls sync orchestration (AC3.4)."""
        with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets:
            mock_sheets.return_value = "1"
            with patch("app.services.quickbooks_sync.sync") as mock_qb:
                mock_qb.return_value = MagicMock(qb_bill_id="QB-123", sync_status={"quickbooks": "ok"})
                with patch("app.services.jobber_sync.sync") as mock_jobber:
                    mock_jobber.return_value = MagicMock(jobber_expense_id="JOB-456", sync_status={"jobber": "ok"})
                    req = {
                        "invoice": make_extracted(),
                        "approved_by": "manager@example.com",
                        "approval_notes": "Approved",
                        "approved_at": "2026-05-13T10:00:00Z",
                        "approval_tier": "manager",
                    }
                    r = client.post("/approval-callback", json=req)
                    assert r.status_code == 200
                    assert r.json()["sheets_row_id"] == "1"


class TestSyncEndpoint:
    """Test /sync endpoint."""

    def test_sync_orchestration_success(self, client):
        """POST /sync runs all three targets with mocked sync modules (AC4.6)."""
        with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets:
            mock_sheets.return_value = "2"
            with patch("app.services.sheets_sync.update_sync_status"):
                with patch("app.services.quickbooks_sync.sync") as mock_qb:
                    mock_qb.return_value = MagicMock(qb_bill_id="QB-999", sync_status={"quickbooks": "ok"})
                    with patch("app.services.jobber_sync.sync") as mock_jobber:
                        mock_jobber.return_value = MagicMock(jobber_expense_id="JOB-888", sync_status={"jobber": "ok"})
                        req = {
                            "invoice": make_extracted(),
                            "approved_by": "manager@example.com",
                            "approval_notes": "Approved",
                            "approved_at": "2026-05-13T10:00:00Z",
                            "approval_tier": "auto",
                        }
                        r = client.post("/sync", json=req)
                        assert r.status_code == 200
                        data = r.json()
                        assert data["sheets_row_id"] == "2"
                        assert data["qb_bill_id"] == "QB-999"
                        assert data["jobber_expense_id"] == "JOB-888"


class TestExtractEndpoint:
    """Test /extract endpoint."""

    def test_extract_rejects_non_pdf(self, client):
        """POST /extract with non-PDF content-type returns 415."""
        # Simulate uploading a non-PDF file
        r = client.post(
            "/extract",
            files={"file": ("test.txt", b"not a pdf", "text/plain")}
        )
        assert r.status_code == 415
        assert "Only PDF files accepted" in r.json()["detail"]

    @patch("app.services.extractor_gemini.parse_and_extract")
    def test_extract_with_valid_pdf_mocked(self, mock_extract, client):
        """POST /extract with valid PDF calls extractor and returns InvoiceExtracted."""
        # Mock the extractor to return a valid invoice
        mock_invoice = InvoiceExtracted(**make_extracted())
        mock_extract.return_value = mock_invoice

        with patch("app.services.vendor_matcher.normalize", return_value="Acme Corp"):
            r = client.post(
                "/extract",
                files={"file": ("test.pdf", b"%PDF-1.4 mock pdf", "application/pdf")}
            )

        assert r.status_code == 200
        data = r.json()
        assert data["vendor_raw"] == "Acme Corp LLC"
        assert data["total"] == 108.0

    @patch("app.services.extractor_gemini.parse_and_extract")
    def test_extract_returns_422_on_extraction_failure(self, mock_extract, client):
        """POST /extract returns 422 if extractor returns None."""
        mock_extract.return_value = None

        r = client.post(
            "/extract",
            files={"file": ("test.pdf", b"%PDF-1.4 mock pdf", "application/pdf")}
        )

        assert r.status_code == 422
        assert "extraction failed" in r.json()["detail"].lower()
