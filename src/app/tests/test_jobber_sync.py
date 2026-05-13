from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest

from app.schemas.invoice import InvoiceExtracted, LineItem, SyncRequest


def make_test_invoice(**overrides) -> InvoiceExtracted:
    base = {
        "vendor_raw": "Test Vendor LLC",
        "vendor_normalized": "Test Vendor",
        "subtotal": 100.0,
        "tax": 10.0,
        "shipping": 0.0,
        "discount": 0.0,
        "total": 110.0,
        "line_items": [],
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "test-hash-456",
        "file_name": "test-invoice.pdf",
        "invoice_number": "JOB-TEST-001",
        "invoice_date": "2026-03-15",
        "job_id": "JOB-12345",
    }
    return InvoiceExtracted(**{**base, **overrides})


def test_jobber_expense_payload_contains_total_and_job_id():
    """Verify Jobber expense payload contains total and jobId (AC4.3)."""
    # Create test invoice with specific total and job_id
    inv = make_test_invoice(
        total=1500.00,
        job_id="JOB-PROJ-789",
    )

    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    # Mock httpx.post to capture the payload
    with patch("app.services.jobber_sync.httpx.post") as mock_post:
        # Set up successful response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "expenseCreate": {
                    "expense": {
                        "id": "exp-123",
                        "description": "Invoice JOB-TEST-001 from Test Vendor",
                        "total": 1500.00,
                    },
                    "userErrors": []
                }
            }
        }
        mock_post.return_value = mock_response

        # Import and call the function
        from app.services.jobber_sync import _create_expense_sync

        expense_id = _create_expense_sync(sync_req, "test-access-token")

        # Verify httpx.post was called
        assert mock_post.called
        call_args = mock_post.call_args

        # Get the json payload that was sent
        json_payload = call_args[1]["json"]  # json is a keyword argument

        # Verify the variables contain total and jobId
        variables = json_payload.get("variables", {})
        expense_input = variables.get("input", {})

        assert expense_input.get("total") == 1500.00, f"Expected total=1500.00, got {expense_input.get('total')}"
        assert expense_input.get("jobId") == "JOB-PROJ-789", f"Expected jobId=JOB-PROJ-789, got {expense_input.get('jobId')}"

        # Verify expense ID was returned
        assert expense_id == "exp-123"
