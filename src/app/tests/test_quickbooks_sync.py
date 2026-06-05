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
        "line_items": [
            LineItem(
                description="Test Line Item",
                quantity=1.0,
                unit_price=100.0,
                line_total=100.0,
                category="materials"
            )
        ],
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "test-hash-123",
        "file_name": "test-invoice.pdf",
        "invoice_number": "QB-TEST-001",
        "invoice_date": "2026-03-15",
    }
    return InvoiceExtracted(**{**base, **overrides})


def test_create_bill_uses_vendor_and_line_items():
    """Verify QB Bill payload uses vendor_normalized and line_items (AC4.2)."""
    # Create a test invoice with specific vendor and line items
    inv = make_test_invoice(
        vendor_normalized="Acme Supply Co",
        line_items=[
            LineItem(
                description="Widgets",
                quantity=10.0,
                unit_price=50.0,
                line_total=500.0,
                category="materials"
            ),
            LineItem(
                description="Labor",
                quantity=5.0,
                unit_price=100.0,
                line_total=500.0,
                category="labor"
            ),
        ]
    )

    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    # Mock settings
    mock_settings = MagicMock()
    mock_settings.qb_client_id = "test-client-id"
    mock_settings.qb_client_secret = "test-client-secret"
    mock_settings.qb_refresh_token = "test-refresh-token"
    mock_settings.qb_realm_id = "test-realm-id"
    mock_settings.qb_default_expense_account_id = "1"

    # Patch the QuickBooks dependencies at the source (intuitlib and quickbooks modules)
    # since they are imported inside the _create_bill_sync function
    with patch("intuitlib.client.AuthClient") as mock_auth_client_class:
        with patch("quickbooks.QuickBooks") as mock_qb_class:
            with patch("quickbooks.objects.vendor.Vendor") as mock_vendor_class:
                with patch("quickbooks.objects.base.Ref") as mock_ref_class:
                    with patch("quickbooks.objects.bill.Bill") as mock_bill_class:
                        with patch("quickbooks.objects.detailline.AccountBasedExpenseLine") as mock_line_class:
                            with patch("quickbooks.objects.detailline.AccountBasedExpenseLineDetail") as mock_detail_class:

                                # Set up vendor mock - return list with one vendor
                                mock_vendor = MagicMock()
                                mock_vendor.Id = "VENDOR-123"
                                mock_vendor_class.where.return_value = [mock_vendor]

                                # No existing Bill — idempotency query returns empty so create proceeds
                                mock_bill_class.where.return_value = []

                                # Set up QB client mock
                                mock_qb_instance = MagicMock()
                                mock_qb_class.return_value = mock_qb_instance

                                # Set up Bill mock
                                mock_bill = MagicMock()
                                mock_bill.Id = "BILL-123"
                                mock_bill_class.return_value = mock_bill

                                # Set up Line mocks - we'll track calls
                                mock_lines = []
                                def make_line(*args, **kwargs):
                                    line = MagicMock()
                                    mock_lines.append(line)
                                    return line

                                mock_line_class.side_effect = make_line

                                # Set up Detail mocks
                                mock_details = []
                                def make_detail(*args, **kwargs):
                                    detail = MagicMock()
                                    mock_details.append(detail)
                                    return detail

                                mock_detail_class.side_effect = make_detail

                                # Set up Ref mocks
                                mock_refs = []
                                def make_ref(*args, **kwargs):
                                    ref = MagicMock()
                                    mock_refs.append(ref)
                                    return ref

                                mock_ref_class.side_effect = make_ref

                                # Import and call the function
                                from app.services.quickbooks_sync import _create_bill_sync

                                bill_id, created = _create_bill_sync(sync_req, mock_settings, "1")

                                # Verify Vendor lookup used normalized name
                                assert mock_vendor_class.where.called
                                call_query = mock_vendor_class.where.call_args[0][0]
                                assert "Acme Supply Co" in call_query

                                # Verify Bill was created and assigned a VendorRef
                                assert mock_bill_class.called
                                bill_instance = mock_bill_class.return_value
                                # VendorRef should have been set on the bill
                                assert bill_instance.VendorRef is not None

                                # Verify lines were created for each line item (2 lines in this test)
                                # Due to the mocking, we expect 2 line objects and 2 detail objects
                                assert len(mock_lines) >= 2, f"Expected at least 2 lines, got {len(mock_lines)}"

                                # Verify bill.save was called with the QB instance
                                assert bill_instance.save.called

                                # Verify bill ID was returned and it was a fresh create
                                assert bill_id == "BILL-123"
                                assert created is True


def test_idempotent_retry_returns_existing_bill_without_creating():
    """A retry of an already-synced invoice returns the existing Bill id and does
    NOT create a second Bill (H-1 idempotency)."""
    inv = make_test_invoice(file_hash="dup-hash-xyz", invoice_number="QB-DUP-001")
    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    mock_settings = MagicMock()
    mock_settings.qb_client_id = "test-client-id"
    mock_settings.qb_client_secret = "test-client-secret"
    mock_settings.qb_refresh_token = "test-refresh-token"
    mock_settings.qb_realm_id = "test-realm-id"
    mock_settings.qb_default_expense_account_id = "1"

    with patch("intuitlib.client.AuthClient"):
        with patch("quickbooks.QuickBooks") as mock_qb_class:
            with patch("quickbooks.objects.vendor.Vendor") as mock_vendor_class:
                with patch("quickbooks.objects.bill.Bill") as mock_bill_class:
                    mock_qb_class.return_value = MagicMock()

                    # An existing Bill tagged with this invoice's file_hash is found.
                    existing_bill = MagicMock()
                    existing_bill.Id = "EXISTING-BILL-99"
                    existing_bill.PrivateNote = "qclerq-file-hash:dup-hash-xyz"
                    mock_bill_class.where.return_value = [existing_bill]

                    # Bill() constructor returns a fresh mock; .save must never be called.
                    new_bill = MagicMock()
                    new_bill.Id = "SHOULD-NOT-BE-CREATED"
                    mock_bill_class.return_value = new_bill

                    from app.services.quickbooks_sync import _create_bill_sync

                    bill_id, created = _create_bill_sync(sync_req, mock_settings, "1")

                    assert bill_id == "EXISTING-BILL-99"
                    assert created is False
                    # No new Bill was saved, and the vendor lookup never ran.
                    assert not new_bill.save.called
                    assert not mock_vendor_class.where.called


def test_idempotency_query_failure_does_not_block_create():
    """If the idempotency query raises, the Bill is still created (H-1: a query
    failure must not block the create)."""
    inv = make_test_invoice(file_hash="qfail-hash", invoice_number="QB-QFAIL-001")
    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    mock_settings = MagicMock()
    mock_settings.qb_default_expense_account_id = "1"

    with patch("intuitlib.client.AuthClient"):
        with patch("quickbooks.QuickBooks") as mock_qb_class:
            with patch("quickbooks.objects.vendor.Vendor") as mock_vendor_class:
                with patch("quickbooks.objects.base.Ref"):
                    with patch("quickbooks.objects.bill.Bill") as mock_bill_class:
                        with patch("quickbooks.objects.detailline.AccountBasedExpenseLine"):
                            with patch("quickbooks.objects.detailline.AccountBasedExpenseLineDetail"):
                                mock_qb_class.return_value = MagicMock()

                                # Idempotency query raises — must fall through to create.
                                mock_bill_class.where.side_effect = RuntimeError("QBO query down")

                                mock_vendor = MagicMock()
                                mock_vendor.Id = "VENDOR-1"
                                mock_vendor_class.where.return_value = [mock_vendor]

                                created_bill = MagicMock()
                                created_bill.Id = "FRESH-BILL-1"
                                mock_bill_class.return_value = created_bill

                                from app.services.quickbooks_sync import _create_bill_sync

                                bill_id, created = _create_bill_sync(sync_req, mock_settings, "1")

                                assert bill_id == "FRESH-BILL-1"
                                assert created is True
                                assert created_bill.save.called


@pytest.mark.asyncio
async def test_sync_returns_skipped_status_on_idempotent_retry():
    """sync() returns the existing qb_bill_id with sync_status 'skipped' on retry."""
    inv = make_test_invoice(file_hash="dup-hash-async", invoice_number="QB-DUP-ASYNC")
    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    mock_settings = MagicMock()
    mock_settings.qb_default_expense_account_id = "1"

    with patch("app.services.quickbooks_sync._create_bill_sync") as mock_create:
        # Simulate the idempotent path: existing id returned, created=False.
        mock_create.return_value = ("EXISTING-BILL-ASYNC", False)

        from app.services.quickbooks_sync import sync

        result = await sync(sync_req, mock_settings)

        assert result.qb_bill_id == "EXISTING-BILL-ASYNC"
        assert result.sync_status["quickbooks"] == "skipped"


@pytest.mark.asyncio
async def test_sync_logs_failure_reason(caplog):
    """A QB sync failure is logged at ERROR with the exception (zero silent failures).

    Regression for the e2e finding: a missing vendor (or any QB error) was swallowed
    with no log, making production failures a black box.
    """
    import logging

    sync_req = SyncRequest(
        invoice=make_test_invoice(),
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )
    mock_settings = MagicMock()
    mock_settings.qb_default_expense_account_id = "1"

    with patch(
        "app.services.quickbooks_sync._create_bill_sync",
        side_effect=ValueError("Vendor 'Hicks Hardware' not found in QuickBooks"),
    ):
        from app.services.quickbooks_sync import sync

        with caplog.at_level(logging.ERROR, logger="app.services.quickbooks_sync"):
            result = await sync(sync_req, mock_settings)

    assert result.sync_status["quickbooks"] == "failed"
    assert result.qb_bill_id is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR log when QB sync fails"
    assert errors[0].exc_info is not None, "the failure reason (exception) must be captured"
