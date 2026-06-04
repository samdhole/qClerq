from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import httpx
import pytest

from app.schemas.invoice import InvoiceExtracted, LineItem, SyncRequest


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        str(status_code), request=MagicMock(), response=MagicMock(status_code=status_code)
    )


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

    # Mock httpx.post to capture the payload. Two calls happen: the idempotency
    # search (returns no match) then the create.
    with patch("app.services.jobber_sync.httpx.post") as mock_post:
        search_response = MagicMock()
        search_response.json.return_value = {"data": {"expenses": {"nodes": []}}}

        create_response = MagicMock()
        create_response.json.return_value = {
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
        mock_post.side_effect = [search_response, create_response]

        # Import and call the function
        from app.services.jobber_sync import _create_expense_sync

        expense_id, created = _create_expense_sync(sync_req, "test-access-token")

        # Verify httpx.post was called for the create (second call)
        assert mock_post.call_count == 2
        create_call_args = mock_post.call_args_list[1]

        # Get the json payload that was sent on the create call
        json_payload = create_call_args[1]["json"]  # json is a keyword argument

        # Verify the variables contain total and jobId
        variables = json_payload.get("variables", {})
        expense_input = variables.get("input", {})

        assert expense_input.get("total") == 1500.00, f"Expected total=1500.00, got {expense_input.get('total')}"
        assert expense_input.get("jobId") == "JOB-PROJ-789", f"Expected jobId=JOB-PROJ-789, got {expense_input.get('jobId')}"

        # Verify expense ID was returned and it was a fresh create
        assert expense_id == "exp-123"
        assert created is True


def test_idempotent_retry_returns_existing_expense_without_creating():
    """A retry of an already-synced invoice returns the existing expense id and
    does NOT create a second expense (H-1 idempotency)."""
    inv = make_test_invoice(file_hash="job-dup-hash", invoice_number="JOB-DUP-001")
    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    with patch("app.services.jobber_sync.httpx.post") as mock_post:
        # The search returns an expense whose description carries this file_hash tag.
        search_response = MagicMock()
        search_response.json.return_value = {
            "data": {
                "expenses": {
                    "nodes": [
                        {
                            "id": "existing-exp-77",
                            "description": "Invoice JOB-DUP-001 from Test Vendor [qclerq-file-hash:job-dup-hash]",
                        }
                    ]
                }
            }
        }
        mock_post.return_value = search_response

        from app.services.jobber_sync import _create_expense_sync

        expense_id, created = _create_expense_sync(sync_req, "test-access-token")

        assert expense_id == "existing-exp-77"
        assert created is False
        # Only the search ran — no create mutation was sent.
        assert mock_post.call_count == 1


def test_idempotency_query_failure_does_not_block_create():
    """If the idempotency search raises (e.g. unsupported query arg), the expense is
    still created (H-1: a query failure must not block the create)."""
    inv = make_test_invoice(file_hash="job-qfail", invoice_number="JOB-QFAIL-001")
    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    with patch("app.services.jobber_sync.httpx.post") as mock_post:
        # First call (the search) raises; second call (the create) succeeds.
        create_response = MagicMock()
        create_response.json.return_value = {
            "data": {
                "expenseCreate": {
                    "expense": {"id": "fresh-exp-1", "total": 110.0},
                    "userErrors": [],
                }
            }
        }
        mock_post.side_effect = [RuntimeError("expenses query unsupported"), create_response]

        from app.services.jobber_sync import _create_expense_sync

        expense_id, created = _create_expense_sync(sync_req, "test-access-token")

        assert expense_id == "fresh-exp-1"
        assert created is True
        # Both the (failed) search and the (successful) create were attempted.
        assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_sync_returns_skipped_status_on_idempotent_retry():
    """sync() returns the existing jobber_expense_id with sync_status 'skipped'."""
    inv = make_test_invoice(file_hash="job-dup-async", invoice_number="JOB-DUP-ASYNC")
    sync_req = SyncRequest(
        invoice=inv,
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )

    mock_settings = MagicMock()

    with patch("app.services.jobber_auth.get_access_token", return_value="tok"), \
         patch("app.services.jobber_sync._create_expense_sync") as mock_create:
        mock_create.return_value = ("existing-exp-async", False)

        from app.services.jobber_sync import sync

        result = await sync(sync_req, mock_settings)

        assert result.jobber_expense_id == "existing-exp-async"
        assert result.sync_status["jobber"] == "skipped"


def _basic_sync_req() -> SyncRequest:
    return SyncRequest(
        invoice=make_test_invoice(),
        approved_by="manager@test.local",
        approval_notes="Test",
        approved_at=datetime.now(timezone.utc),
        approval_tier="manager",
    )


@pytest.mark.asyncio
async def test_sync_obtains_access_token_via_token_manager():
    """sync() pulls a fresh token from jobber_auth instead of a static settings field."""
    with patch("app.services.jobber_auth.get_access_token", return_value="fresh-token") as get_tok, \
         patch("app.services.jobber_sync._create_expense_sync") as mock_create:
        mock_create.return_value = ("exp-1", True)

        from app.services.jobber_sync import sync

        result = await sync(_basic_sync_req(), MagicMock())

        get_tok.assert_called_once()
        # the token from the manager (not a static field) is what reaches the create
        assert mock_create.call_args.args[1] == "fresh-token"
        assert result.sync_status["jobber"] == "ok"


@pytest.mark.asyncio
async def test_sync_retries_once_after_401_with_fresh_token():
    """A 401 invalidates the cached token, gets a fresh one, and retries the create once."""
    with patch("app.services.jobber_auth.get_access_token", side_effect=["stale", "renewed"]) as get_tok, \
         patch("app.services.jobber_auth.invalidate") as invalidate, \
         patch("app.services.jobber_sync._create_expense_sync") as mock_create:
        mock_create.side_effect = [_http_status_error(401), ("exp-after-retry", True)]

        from app.services.jobber_sync import sync

        result = await sync(_basic_sync_req(), MagicMock())

        assert result.jobber_expense_id == "exp-after-retry"
        assert result.sync_status["jobber"] == "ok"
        invalidate.assert_called_once()
        assert get_tok.call_count == 2
        assert mock_create.call_count == 2
        assert mock_create.call_args_list[1].args[1] == "renewed"


@pytest.mark.asyncio
async def test_sync_non_401_error_fails_without_retry():
    """A non-auth error marks jobber failed and does not retry."""
    with patch("app.services.jobber_auth.get_access_token", return_value="tok"), \
         patch("app.services.jobber_auth.invalidate") as invalidate, \
         patch("app.services.jobber_sync._create_expense_sync") as mock_create:
        mock_create.side_effect = _http_status_error(500)

        from app.services.jobber_sync import sync

        result = await sync(_basic_sync_req(), MagicMock())

        assert result.jobber_expense_id is None
        assert result.sync_status["jobber"] == "failed"
        invalidate.assert_not_called()
        assert mock_create.call_count == 1
