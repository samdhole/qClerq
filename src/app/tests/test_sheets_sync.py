from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import json

import pytest

from app.schemas.invoice import InvoiceExtracted, ExceptionItem, LineItem


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
        "file_hash": "sheets-test-hash",
        "file_name": "test-invoice.pdf",
        "invoice_number": "SHEETS-001",
        "invoice_date": "2026-03-15",
    }
    return InvoiceExtracted(**{**base, **overrides})


def test_write_exceptions_row_structure():
    """Verify Exceptions-tab row contains exception type, severity, message, and status (AC6.2)."""
    inv = make_test_invoice()
    exceptions = [
        ExceptionItem(
            type="high_confidence_gap",
            severity="high",
            message="Confidence below 80%",
        ),
        ExceptionItem(
            type="missing_field",
            severity="medium",
            message="Invoice date missing",
        ),
    ]

    service_account_json = json.dumps({"type": "service_account", "project_id": "test"})

    with patch("app.services.sheets_sync.gspread.service_account_from_dict") as mock_gspread:
        # Set up mock client and worksheet
        mock_ws = MagicMock()
        mock_sheet = MagicMock()
        mock_client = MagicMock()

        mock_gspread.return_value = mock_client
        mock_client.open_by_key.return_value = mock_sheet
        mock_sheet.worksheet.return_value = mock_ws

        # Import and call the function
        from app.services.sheets_sync import write_exceptions

        write_exceptions("test-sheet-id", inv, exceptions, service_account_json)

        # Verify append_row was called for each exception
        assert mock_ws.append_row.call_count == 2

        # Check first exception row
        first_call_args = mock_ws.append_row.call_args_list[0]
        first_row = first_call_args[0][0]

        # Structure: file_hash, file_name, vendor_normalized, invoice_number, issue_type, severity, message, status, created_at
        assert len(first_row) == 9, f"Expected 9 columns, got {len(first_row)}"
        assert first_row[0] == "sheets-test-hash", "file_hash mismatch"
        assert first_row[1] == "test-invoice.pdf", "file_name mismatch"
        assert first_row[2] == "Test Vendor", "vendor_normalized mismatch"
        assert first_row[3] == "SHEETS-001", "invoice_number mismatch"
        assert first_row[4] == "high_confidence_gap", "issue_type mismatch"
        assert first_row[5] == "high", "severity mismatch"
        assert first_row[6] == "Confidence below 80%", "message mismatch"
        assert first_row[7] == "open", "status should be 'open'"

        # Check second exception row
        second_call_args = mock_ws.append_row.call_args_list[1]
        second_row = second_call_args[0][0]
        assert second_row[4] == "missing_field", "second exception type mismatch"
        assert second_row[5] == "medium", "second exception severity mismatch"
        assert second_row[6] == "Invoice date missing", "second exception message mismatch"


def test_write_invoice_row_includes_file_hash():
    """Verify write_invoice_row includes file_hash as first column (AC6.3)."""
    from datetime import date
    from app.schemas.invoice import SyncRequest

    inv = make_test_invoice(
        file_hash="unique-file-hash-789",
        invoice_date=date(2026, 3, 15),
    )

    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test approval",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    service_account_json = json.dumps({"type": "service_account", "project_id": "test"})

    with patch("app.services.sheets_sync.gspread.service_account_from_dict") as mock_gspread:
        # Set up mock client and worksheet
        mock_ws = MagicMock()
        mock_sheet = MagicMock()
        mock_client = MagicMock()

        mock_gspread.return_value = mock_client
        mock_client.open_by_key.return_value = mock_sheet
        mock_sheet.worksheet.return_value = mock_ws
        # After append_row is called, get_all_values returns the updated list (header + 2 data rows)
        mock_ws.get_all_values.return_value = [
            ["file_hash", "file_name", "invoice_number"],
            ["row1", "data1", "inv1"],
            ["unique-file-hash-789", "test-invoice.pdf", "SHEETS-001"]
        ]

        # Import and call the function
        from app.services.sheets_sync import write_invoice_row

        row_id = write_invoice_row(
            "test-sheet-id",
            sync_req,
            "QB-ID",
            "JOB-ID",
            {"sheets": "ok"},
            service_account_json,
        )

        # Verify append_row was called
        assert mock_ws.append_row.called
        call_args = mock_ws.append_row.call_args
        row_data = call_args[0][0]

        # First element should be the file_hash
        assert row_data[0] == "unique-file-hash-789", f"Expected file_hash at position 0, got {row_data[0]}"

        # Verify row_id is returned (should be string of row count after append)
        # The function returns len(all_rows), which is 3 (header + 2 data rows)
        assert row_id == "3", f"Expected row_id='3', got {row_id}"
