from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

from app.schemas.invoice import SyncRequest, SyncResult


def make_sync_request(**overrides) -> dict:
    base = {
        "invoice": {
            "vendor_raw": "Acme Corp LLC",
            "vendor_normalized": "Acme Corp",
            "subtotal": 100.0, "tax": 8.0, "shipping": 0.0, "discount": 0.0,
            "total": 108.0, "line_items": [], "confidence_overall": 0.95,
            "duplicate_risk": "none", "missing_required_fields": [],
            "warnings": [], "file_hash": "abc123", "file_name": "invoice.pdf",
            "invoice_number": "INV-001", "invoice_date": "2026-01-15",
        },
        "approved_by": "manager@test.local",
        "approval_notes": "Approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approval_tier": "auto",
    }
    return {**base, **overrides}


@pytest.mark.asyncio
async def test_sync_all_sheets_success(client):
    """Verify Sheets row is written with initial sync_status (AC4.1)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "1"
        with patch("app.services.quickbooks_sync.sync") as mock_qb:
            mock_qb.return_value = SyncResult(
                qb_bill_id="QB-123",
                sync_status={"quickbooks": "ok"}
            )
            with patch("app.services.jobber_sync.sync") as mock_jobber:
                mock_jobber.return_value = SyncResult(
                    jobber_expense_id="JOB-456",
                    sync_status={"jobber": "ok"}
                )

                req_data = make_sync_request()
                response = client.post("/sync", json=req_data)

                assert response.status_code == 200
                result = response.json()
                assert result["sheets_row_id"] == "1"
                assert result["sync_status"]["sheets"] == "ok"


@pytest.mark.asyncio
async def test_sync_all_qb_failure_does_not_block_jobber(client):
    """Verify QB sync failure sets sync_status['quickbooks']='failed' without blocking Jobber (AC4.4)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "2"
        with patch("app.services.quickbooks_sync.sync") as mock_qb:
            # QB sync fails
            mock_qb.side_effect = Exception("QB connection error")
            with patch("app.services.jobber_sync.sync") as mock_jobber:
                # Jobber sync succeeds
                mock_jobber.return_value = SyncResult(
                    jobber_expense_id="JOB-789",
                    sync_status={"jobber": "ok"}
                )

                req_data = make_sync_request()
                response = client.post("/sync", json=req_data)

                assert response.status_code == 200
                result = response.json()
                assert result["sync_status"]["quickbooks"] == "failed"
                assert result["sync_status"]["jobber"] == "ok"
                assert result["jobber_expense_id"] == "JOB-789"
                # Verify Jobber sync was still called
                mock_jobber.assert_called_once()


@pytest.mark.asyncio
async def test_sync_all_jobber_failure_does_not_block_sheets(client):
    """Verify Jobber sync failure sets sync_status['jobber']='failed' without blocking (AC4.5)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "3"
        with patch("app.services.quickbooks_sync.sync") as mock_qb:
            mock_qb.return_value = SyncResult(
                qb_bill_id="QB-999",
                sync_status={"quickbooks": "ok"}
            )
            with patch("app.services.jobber_sync.sync") as mock_jobber:
                # Jobber sync fails
                mock_jobber.side_effect = Exception("Jobber API error")

                req_data = make_sync_request()
                response = client.post("/sync", json=req_data)

                assert response.status_code == 200
                result = response.json()
                assert result["sync_status"]["jobber"] == "failed"
                assert result["sync_status"]["quickbooks"] == "ok"
                assert result["qb_bill_id"] == "QB-999"
                assert result["sheets_row_id"] == "3"


@pytest.mark.asyncio
async def test_sync_all_backfill_sync_status(client):
    """Verify final sync_status is written back to Sheets row (AC4.6)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "4"
        with patch("app.services.sheets_sync.update_sync_status") as mock_update:
            with patch("app.services.quickbooks_sync.sync") as mock_qb:
                mock_qb.return_value = SyncResult(
                    qb_bill_id="QB-111",
                    sync_status={"quickbooks": "ok"}
                )
                with patch("app.services.jobber_sync.sync") as mock_jobber:
                    mock_jobber.return_value = SyncResult(
                        jobber_expense_id="JOB-222",
                        sync_status={"jobber": "ok"}
                    )

                    req_data = make_sync_request()
                    response = client.post("/sync", json=req_data)

                    assert response.status_code == 200
                    # Verify update_sync_status was called with final status
                    mock_update.assert_called_once()
                    call_args = mock_update.call_args
                    # check that sync_status contains all three targets
                    sync_status_arg = call_args[0][2]  # 3rd positional argument
                    assert "sheets" in sync_status_arg
                    assert "quickbooks" in sync_status_arg
                    assert "jobber" in sync_status_arg


@pytest.mark.asyncio
async def test_sync_all_sheets_failure_still_attempts_other_targets(client):
    """Verify Sheets write failure doesn't prevent QB/Jobber from running."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.side_effect = Exception("Sheets error")
        with patch("app.services.quickbooks_sync.sync") as mock_qb:
            mock_qb.return_value = SyncResult(
                qb_bill_id="QB-333",
                sync_status={"quickbooks": "ok"}
            )
            with patch("app.services.jobber_sync.sync") as mock_jobber:
                mock_jobber.return_value = SyncResult(
                    jobber_expense_id="JOB-444",
                    sync_status={"jobber": "ok"}
                )

                req_data = make_sync_request()
                response = client.post("/sync", json=req_data)

                assert response.status_code == 200
                result = response.json()
                assert result["sync_status"]["sheets"] == "failed"
                assert result["sync_status"]["quickbooks"] == "ok"
                assert result["sync_status"]["jobber"] == "ok"
                # Both QB and Jobber should have been called despite Sheets failure
                mock_qb.assert_called_once()
                mock_jobber.assert_called_once()


@pytest.mark.asyncio
async def test_sync_all_returns_all_ids_on_success(client):
    """Verify SyncResult contains all three IDs when all targets succeed (AC4.6)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "5"
        with patch("app.services.sheets_sync.update_sync_status"):
            with patch("app.services.quickbooks_sync.sync") as mock_qb:
                mock_qb.return_value = SyncResult(
                    qb_bill_id="QB-555",
                    sync_status={"quickbooks": "ok"}
                )
                with patch("app.services.jobber_sync.sync") as mock_jobber:
                    mock_jobber.return_value = SyncResult(
                        jobber_expense_id="JOB-666",
                        sync_status={"jobber": "ok"}
                    )

                    req_data = make_sync_request()
                    response = client.post("/sync", json=req_data)

                    assert response.status_code == 200
                    result = response.json()
                    assert result["sheets_row_id"] == "5"
                    assert result["qb_bill_id"] == "QB-555"
                    assert result["jobber_expense_id"] == "JOB-666"
                    assert result["sync_status"]["sheets"] == "ok"
                    assert result["sync_status"]["quickbooks"] == "ok"
                    assert result["sync_status"]["jobber"] == "ok"


def test_sync_all_sheets_row_contains_invoice_fields(client):
    """Verify Sheets row payload contains required invoice fields (AC4.1)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "6"
        with patch("app.services.quickbooks_sync.sync") as mock_qb:
            mock_qb.return_value = SyncResult(
                qb_bill_id="QB-666",
                sync_status={"quickbooks": "ok"}
            )
            with patch("app.services.jobber_sync.sync") as mock_jobber:
                mock_jobber.return_value = SyncResult(
                    jobber_expense_id="JOB-777",
                    sync_status={"jobber": "ok"}
                )
                with patch("app.services.sheets_sync.update_sync_status"):
                    req_data = make_sync_request(
                        invoice={
                            **make_sync_request()["invoice"],
                            "vendor_normalized": "Test Vendor Inc",
                            "total": 1234.56,
                            "invoice_number": "INV-999",
                            "invoice_date": "2026-03-15",
                        }
                    )
                    response = client.post("/sync", json=req_data)

                    assert response.status_code == 200
                    # Capture the args passed to write_invoice_row
                    assert mock_sheets_write.called
                    call_args = mock_sheets_write.call_args
                    sync_req = call_args[0][1]  # 2nd positional arg is SyncRequest

                    # Verify required invoice fields are in the request
                    assert sync_req.invoice.vendor_normalized == "Test Vendor Inc"
                    assert sync_req.invoice.total == 1234.56
                    assert sync_req.invoice.invoice_number == "INV-999"
                    assert sync_req.invoice.invoice_date is not None


@pytest.mark.asyncio
async def test_sync_all_sheets_row_has_proof_trail_columns(client):
    """Verify Sheets row has proof trail columns (AC6.1)."""
    with patch("app.services.sheets_sync.write_invoice_row") as mock_sheets_write:
        mock_sheets_write.return_value = "7"
        with patch("app.services.sheets_sync.update_sync_status") as mock_update_status:
            with patch("app.services.quickbooks_sync.sync") as mock_qb:
                mock_qb.return_value = SyncResult(
                    qb_bill_id="QB-777",
                    sync_status={"quickbooks": "ok"}
                )
                with patch("app.services.jobber_sync.sync") as mock_jobber:
                    mock_jobber.return_value = SyncResult(
                        jobber_expense_id="JOB-888",
                        sync_status={"jobber": "ok"}
                    )

                    req_data = make_sync_request(
                        approved_by="manager@test.local",
                        approval_tier="auto",
                    )
                    response = client.post("/sync", json=req_data)

                    assert response.status_code == 200
                    # Verify write_invoice_row was called with proof trail data
                    assert mock_sheets_write.called
                    call_args = mock_sheets_write.call_args
                    sync_req = call_args[0][1]

                    assert sync_req.approved_by == "manager@test.local"
                    assert sync_req.approval_tier == "auto"
                    assert sync_req.approved_at is not None

                    # Verify update_sync_status was called with sync_status
                    assert mock_update_status.called
                    update_call_args = mock_update_status.call_args
                    sync_status_arg = update_call_args[0][2]  # 3rd positional argument
                    assert isinstance(sync_status_arg, dict)
                    assert "sheets" in sync_status_arg
