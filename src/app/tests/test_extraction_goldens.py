"""Tests for invoice extraction pipeline.

Verifies:
- AC2.1: Valid text returns InvoiceExtracted with all required fields
- AC2.2: confidence_overall reflects readability
- AC2.7: LlamaParse fallback to PDF.co when LlamaParse raises
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.schemas.invoice import InvoiceExtracted, LineItem
from app.services.extractor_claude import extract, parse_and_extract


def make_claude_tool_response(data: dict) -> MagicMock:
    """Create a mocked Claude tool_use response."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = data
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


def make_valid_tool_input() -> dict:
    """Return a minimal valid tool input matching InvoiceExtracted schema."""
    return {
        "invoice_number": "INV-001",
        "invoice_date": "2026-05-13",
        "due_date": "2026-06-13",
        "vendor_raw": "Acme Corp",
        "vendor_normalized": "Acme Corp",
        "po_number": "PO-123",
        "job_id": "JOB-456",
        "subtotal": 100.0,
        "tax": 10.0,
        "shipping": 5.0,
        "discount": 0.0,
        "total": 115.0,
        "currency": "USD",
        "line_items": [
            {
                "description": "Widget",
                "quantity": 1.0,
                "unit_price": 100.0,
                "line_total": 100.0,
                "category": "materials",
            }
        ],
        "payment_terms": "Net 30",
        "confidence_overall": 0.95,
        "duplicate_risk": "none",
        "missing_required_fields": [],
        "warnings": [],
        "file_hash": "abc123",
        "file_name": "invoice.pdf",
    }


class TestExtract:
    """Test extract() function."""

    def test_ac21_valid_text_returns_invoice_extracted(self) -> None:
        """AC2.1: Valid text returns InvoiceExtracted with all required fields."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.return_value = make_claude_tool_response(
                make_valid_tool_input()
            )

            result = extract(
                raw_text="Invoice for 100 widgets",
                file_hash="abc123",
                file_name="invoice.pdf",
                api_key="test-key",
            )

            assert result is not None
            assert isinstance(result, InvoiceExtracted)
            assert result.vendor_raw == "Acme Corp"
            assert result.total == 115.0
            assert result.file_hash == "abc123"
            assert result.file_name == "invoice.pdf"

    def test_ac22_high_confidence_for_clean_invoice(self) -> None:
        """AC2.2: Clean digital PDF → confidence_overall >= 0.7."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            data = make_valid_tool_input()
            data["confidence_overall"] = 0.95
            mock_instance.messages.create.return_value = make_claude_tool_response(data)

            result = extract(
                raw_text="Clean digital invoice",
                file_hash="hash1",
                file_name="clean.pdf",
                api_key="test-key",
            )

            assert result is not None
            assert result.confidence_overall >= 0.7

    def test_ac22_low_confidence_for_poor_quality(self) -> None:
        """AC2.2: Scanned/poor quality → confidence_overall < 0.7."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            data = make_valid_tool_input()
            data["confidence_overall"] = 0.4
            mock_instance.messages.create.return_value = make_claude_tool_response(data)

            result = extract(
                raw_text="Blurry scanned invoice with poor OCR",
                file_hash="hash2",
                file_name="scanned.pdf",
                api_key="test-key",
            )

            assert result is not None
            assert result.confidence_overall < 0.7

    def test_extract_returns_none_on_client_exception(self) -> None:
        """extract() returns None on any client exception."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.side_effect = Exception("API error")

            result = extract(
                raw_text="Some invoice text",
                file_hash="hash3",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None

    def test_extract_returns_none_on_wrong_stop_reason(self) -> None:
        """extract() returns None when stop_reason != 'tool_use'."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            response = MagicMock()
            response.stop_reason = "end_turn"
            response.content = []
            mock_instance.messages.create.return_value = response

            result = extract(
                raw_text="Some text",
                file_hash="hash4",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None

    def test_extract_returns_none_on_missing_tool_block(self) -> None:
        """extract() returns None when no tool_use block in response."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            response = MagicMock()
            response.stop_reason = "tool_use"
            response.content = []  # No tool blocks
            mock_instance.messages.create.return_value = response

            result = extract(
                raw_text="Some text",
                file_hash="hash5",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None

    def test_extract_returns_none_on_validation_failure(self) -> None:
        """extract() returns None when Pydantic validation fails."""
        with patch("anthropic.Anthropic") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            # Missing required fields
            bad_data = {"vendor_raw": "test"}
            mock_instance.messages.create.return_value = make_claude_tool_response(bad_data)

            result = extract(
                raw_text="Some text",
                file_hash="hash6",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None


class TestParseAndExtract:
    """Test parse_and_extract() with fallback logic."""

    def test_ac27_llamaparse_success(self) -> None:
        """AC2.7: On LlamaParse success, extract the result."""
        with patch("anthropic.Anthropic") as mock_client, \
             patch("app.services.parser_llamaparse.parse_pdf") as mock_llama, \
             patch("app.services.parser_pdfco.parse_pdf") as mock_pdfco:

            mock_llama.return_value = "Invoice for 100 widgets at $100 each"
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.return_value = make_claude_tool_response(
                make_valid_tool_input()
            )

            result = parse_and_extract(
                pdf_bytes=b"fake pdf",
                file_hash="hash7",
                file_name="test.pdf",
                llama_api_key="llama-key",
                pdfco_api_key="pdfco-key",
                anthropic_api_key="claude-key",
            )

            assert result is not None
            mock_llama.assert_called_once()
            mock_pdfco.assert_not_called()

    def test_ac27_llamaparse_fails_fallback_to_pdfco(self) -> None:
        """AC2.7: When LlamaParse raises, fall back to PDF.co."""
        with patch("anthropic.Anthropic") as mock_client, \
             patch("app.services.parser_llamaparse.parse_pdf") as mock_llama, \
             patch("app.services.parser_pdfco.parse_pdf") as mock_pdfco:

            mock_llama.side_effect = RuntimeError("llamaparse failed")
            mock_pdfco.return_value = "Invoice text from pdfco"
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.return_value = make_claude_tool_response(
                make_valid_tool_input()
            )

            result = parse_and_extract(
                pdf_bytes=b"fake pdf",
                file_hash="hash8",
                file_name="test.pdf",
                llama_api_key="llama-key",
                pdfco_api_key="pdfco-key",
                anthropic_api_key="claude-key",
            )

            assert result is not None
            mock_llama.assert_called_once()
            mock_pdfco.assert_called_once()

    def test_ac27_both_parsers_fail_returns_none(self) -> None:
        """AC2.7: When both parsers fail, return None."""
        with patch("app.services.parser_llamaparse.parse_pdf") as mock_llama, \
             patch("app.services.parser_pdfco.parse_pdf") as mock_pdfco:

            mock_llama.side_effect = RuntimeError("llamaparse failed")
            mock_pdfco.side_effect = RuntimeError("pdfco failed")

            result = parse_and_extract(
                pdf_bytes=b"fake pdf",
                file_hash="hash9",
                file_name="test.pdf",
                llama_api_key="llama-key",
                pdfco_api_key="pdfco-key",
                anthropic_api_key="claude-key",
            )

            assert result is None

    def test_parse_and_extract_empty_text_returns_none(self) -> None:
        """When parser returns empty text, return None."""
        with patch("app.services.parser_llamaparse.parse_pdf") as mock_llama:
            mock_llama.return_value = ""

            result = parse_and_extract(
                pdf_bytes=b"fake pdf",
                file_hash="hash10",
                file_name="test.pdf",
                llama_api_key="llama-key",
                pdfco_api_key="pdfco-key",
                anthropic_api_key="claude-key",
            )

            assert result is None

    def test_parse_and_extract_extraction_failure_returns_none(self) -> None:
        """When extraction fails, return None."""
        with patch("anthropic.Anthropic") as mock_client, \
             patch("app.services.parser_llamaparse.parse_pdf") as mock_llama:

            mock_llama.return_value = "Some invoice text"
            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.return_value = make_claude_tool_response(
                {"vendor_raw": "incomplete"}  # Missing required fields
            )

            result = parse_and_extract(
                pdf_bytes=b"fake pdf",
                file_hash="hash11",
                file_name="test.pdf",
                llama_api_key="llama-key",
                pdfco_api_key="pdfco-key",
                anthropic_api_key="claude-key",
            )

            assert result is None
