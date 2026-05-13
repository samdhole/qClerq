"""Tests for weekly report generation."""
from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.invoice import WeeklySummary
from app.services.report_generator import _generate_sync


def make_sheets_row(**overrides) -> dict:
    """Create a test invoice row."""
    today = date.today()
    base = {
        "invoice_date": str(today - timedelta(days=3)),
        "total": 500.0,
        "vendor_normalized": "Acme Corp",
        "sync_status": '{"sheets":"ok","quickbooks":"ok","jobber":"ok"}',
    }
    return {**base, **overrides}


class TestEmptyWeek:
    """Test AC7.3: Empty week returns valid summary."""

    def test_empty_invoices_returns_valid_summary(self):
        """When Invoices tab has no rows, return zeroed summary."""
        with patch("app.services.report_generator._get_sheets_client") as mock_gc:
            # Setup mock
            ws = MagicMock()
            ws.get_all_records.return_value = []
            sh = MagicMock()
            sh.worksheet.return_value = ws
            mock_gc.return_value.open_by_key.return_value = sh

            # Act
            today = date.today()
            result = _generate_sync("test-id", today - timedelta(7), today, "{}")

            # Assert
            assert isinstance(result, WeeklySummary)
            assert result.invoice_count == 0
            assert result.total_value == 0.0
            assert result.exception_rate == 0.0
            assert result.sync_failures == {"sheets": 0, "quickbooks": 0, "jobber": 0}
            assert result.top_vendors == []


class TestWeekWithInvoices:
    """Test AC7.1: Generate weekly summary with actual data."""

    def test_generate_summary_with_invoices(self):
        """When Invoices tab has rows, return aggregated summary."""
        with patch("app.services.report_generator._get_sheets_client") as mock_gc:
            today = date.today()
            period_start = today - timedelta(days=7)
            period_end = today - timedelta(days=1)

            # Setup invoice rows
            invoices = [
                make_sheets_row(
                    invoice_date=str(today - timedelta(days=5)),
                    total=500.0,
                    vendor_normalized="Acme Corp",
                ),
                make_sheets_row(
                    invoice_date=str(today - timedelta(days=3)),
                    total=1200.0,
                    vendor_normalized="Acme Corp",
                ),
                make_sheets_row(
                    invoice_date=str(today - timedelta(days=2)),
                    total=800.0,
                    vendor_normalized="Supply Co",
                ),
            ]

            # Setup exceptions
            exc_rows = [
                {
                    "created_at": str(today - timedelta(days=4)) + "T10:00:00Z",
                    "type": "low_confidence",
                }
            ]

            # Setup mock sheets
            inv_ws = MagicMock()
            inv_ws.get_all_records.return_value = invoices
            exc_ws = MagicMock()
            exc_ws.get_all_records.return_value = exc_rows

            sh = MagicMock()

            def worksheet_side_effect(name: str):
                if name == "Invoices":
                    return inv_ws
                elif name == "Exceptions":
                    return exc_ws
                raise ValueError(f"Unknown worksheet: {name}")

            sh.worksheet.side_effect = worksheet_side_effect
            mock_gc.return_value.open_by_key.return_value = sh

            # Act
            result = _generate_sync("test-id", period_start, period_end, "{}")

            # Assert
            assert result.invoice_count == 3
            assert result.total_value == 2500.0
            assert result.exception_rate == pytest.approx(1 / 3, abs=0.01)
            assert result.sync_failures == {"sheets": 0, "quickbooks": 0, "jobber": 0}
            assert set(result.top_vendors) == {"Acme Corp", "Supply Co"}

    def test_sync_failures_counted(self):
        """Sync failures per target are correctly counted."""
        with patch("app.services.report_generator._get_sheets_client") as mock_gc:
            today = date.today()
            period_start = today - timedelta(days=7)
            period_end = today - timedelta(days=1)

            invoices = [
                make_sheets_row(
                    invoice_date=str(today - timedelta(days=5)),
                    sync_status='{"sheets":"ok","quickbooks":"failed","jobber":"ok"}',
                ),
                make_sheets_row(
                    invoice_date=str(today - timedelta(days=3)),
                    sync_status='{"sheets":"failed","quickbooks":"ok","jobber":"failed"}',
                ),
            ]

            inv_ws = MagicMock()
            inv_ws.get_all_records.return_value = invoices
            exc_ws = MagicMock()
            exc_ws.get_all_records.return_value = []

            sh = MagicMock()

            def worksheet_side_effect(name: str):
                if name == "Invoices":
                    return inv_ws
                elif name == "Exceptions":
                    return exc_ws
                raise ValueError(f"Unknown worksheet: {name}")

            sh.worksheet.side_effect = worksheet_side_effect
            mock_gc.return_value.open_by_key.return_value = sh

            # Act
            result = _generate_sync("test-id", period_start, period_end, "{}")

            # Assert
            assert result.sync_failures["sheets"] == 1
            assert result.sync_failures["quickbooks"] == 1
            assert result.sync_failures["jobber"] == 1

    def test_top_vendors_limited_to_five(self):
        """Only top 5 vendors returned."""
        with patch("app.services.report_generator._get_sheets_client") as mock_gc:
            today = date.today()
            period_start = today - timedelta(days=7)
            period_end = today - timedelta(days=1)

            # Create 7 vendors, with varying frequencies
            invoices = []
            vendors = ["A", "B", "C", "D", "E", "F", "G"]
            for i, vendor in enumerate(vendors):
                for _ in range(7 - i):
                    invoices.append(
                        make_sheets_row(
                            invoice_date=str(today - timedelta(days=5)),
                            vendor_normalized=vendor,
                        )
                    )

            inv_ws = MagicMock()
            inv_ws.get_all_records.return_value = invoices
            exc_ws = MagicMock()
            exc_ws.get_all_records.return_value = []

            sh = MagicMock()

            def worksheet_side_effect(name: str):
                if name == "Invoices":
                    return inv_ws
                elif name == "Exceptions":
                    return exc_ws
                raise ValueError(f"Unknown worksheet: {name}")

            sh.worksheet.side_effect = worksheet_side_effect
            mock_gc.return_value.open_by_key.return_value = sh

            # Act
            result = _generate_sync("test-id", period_start, period_end, "{}")

            # Assert
            assert len(result.top_vendors) == 5
            # Verify order (most frequent first)
            assert result.top_vendors[0] == "A"  # 7 times
            assert result.top_vendors[1] == "B"  # 6 times
            assert result.top_vendors[-1] == "E"  # 3 times


class TestDateFiltering:
    """Test date range filtering works correctly."""

    def test_invoices_outside_period_excluded(self):
        """Invoices outside the report period are excluded."""
        with patch("app.services.report_generator._get_sheets_client") as mock_gc:
            today = date.today()
            period_start = today - timedelta(days=7)
            period_end = today - timedelta(days=1)

            invoices = [
                # Outside period (too old)
                make_sheets_row(invoice_date=str(today - timedelta(days=10))),
                # Inside period
                make_sheets_row(invoice_date=str(today - timedelta(days=5))),
                # Outside period (too new - today)
                make_sheets_row(invoice_date=str(today)),
            ]

            inv_ws = MagicMock()
            inv_ws.get_all_records.return_value = invoices
            exc_ws = MagicMock()
            exc_ws.get_all_records.return_value = []

            sh = MagicMock()

            def worksheet_side_effect(name: str):
                if name == "Invoices":
                    return inv_ws
                elif name == "Exceptions":
                    return exc_ws
                raise ValueError(f"Unknown worksheet: {name}")

            sh.worksheet.side_effect = worksheet_side_effect
            mock_gc.return_value.open_by_key.return_value = sh

            # Act
            result = _generate_sync("test-id", period_start, period_end, "{}")

            # Assert - only middle invoice counted
            assert result.invoice_count == 1
            assert result.total_value == 500.0
