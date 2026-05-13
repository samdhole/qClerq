"""Tests for invoice extraction pipeline.

Verifies:
- AC2.1: Valid text returns InvoiceExtracted with all required fields
- AC2.2: confidence_overall reflects readability
- AC2.7: LlamaParse fallback to PDF.co when LlamaParse raises
- AC5.1: Vendor normalization exact lookup against vendors.json
- AC5.2: Vendor normalization fuzzy matching via rapidfuzz at ≥88 score
- AC5.3: Vendor normalization LLM fallback with auto-promotion to vendors.json
- AC5.4: Vendor normalization fallback to vendor_raw when all layers fail
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.invoice import InvoiceExtracted, LineItem
from app.services.extractor_claude import extract, parse_and_extract
from app.services.vendor_matcher import normalize


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


# Vendor normalization tests (AC5.1-AC5.4)


@pytest.fixture
def vendor_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a temp vendors.json for testing."""
    data = {
        "Acme Corp LLC": "Acme Corp",
        "Acme Corp.": "Acme Corp",
        "ACME CORPORATION": "Acme Corp",
        "The Home Depot Inc.": "The Home Depot",
        "Home Depot": "The Home Depot",
        "W.W. Grainger Inc.": "W.W. Grainger",
        "Lowe's Companies Inc": "Lowe's",
    }
    p = tmp_path / "vendors.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestVendorNormalization:
    """Test vendor normalization cascade (AC5.1-AC5.4)."""

    def test_ac51_exact_match_case_sensitive(self, vendor_file: pathlib.Path) -> None:
        """AC5.1: Exact case-sensitive lookup returns canonical name."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            result = normalize("Acme Corp LLC")
            assert result == "Acme Corp"

    def test_ac51_exact_match_case_insensitive(self, vendor_file: pathlib.Path) -> None:
        """AC5.1: Case-insensitive exact lookup returns canonical name."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            result = normalize("acme corp llc")
            assert result == "Acme Corp"

    def test_ac51_exact_match_all_caps(self, vendor_file: pathlib.Path) -> None:
        """AC5.1: ALL CAPS variant returns canonical name."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            result = normalize("ACME CORPORATION")
            assert result == "Acme Corp"

    def test_ac52_fuzzy_match_dots_in_name(self, vendor_file: pathlib.Path) -> None:
        """AC5.2: Fuzzy match normalizes punctuation (WW Grainger vs W.W. Grainger)."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            # "WW Grainger" vs "W.W. Grainger" should match at >95 via token_sort_ratio
            result = normalize("WW Grainger")
            assert result == "W.W. Grainger"

    def test_ac52_fuzzy_match_with_suffix_variation(self, vendor_file: pathlib.Path) -> None:
        """AC5.2: Fuzzy match works with variant suffix (Lowe's Company vs Lowe's)."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            # "Lowe's Company" after cleanco strip → "Lowe's" should match exactly
            result = normalize("Lowe's Company")
            assert result == "Lowe's"

    def test_ac53_novel_vendor_llm_success(self, vendor_file: pathlib.Path) -> None:
        """AC5.3: Novel vendor triggers LLM fallback, result promoted to vendors.json."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("anthropic.Anthropic") as mock_client:

            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.content = [MagicMock()]
            mock_response.content[0].text = "Novel Vendor Corp"
            mock_instance.messages.create.return_value = mock_response

            result = normalize("UnknownVendor XYZ", anthropic_api_key="test-key")

            assert result == "Novel Vendor Corp"
            # Verify it was promoted to vendors.json
            data = json.loads(vendor_file.read_text(encoding="utf-8"))
            assert data["UnknownVendor XYZ"] == "Novel Vendor Corp"

    def test_ac53_llm_called_with_known_canonicals(self, vendor_file: pathlib.Path) -> None:
        """AC5.3: LLM receives the list of known canonical names."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("anthropic.Anthropic") as mock_client:

            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.content = [MagicMock()]
            mock_response.content[0].text = "Novel Vendor"
            mock_instance.messages.create.return_value = mock_response

            normalize("ForeignVendor", anthropic_api_key="test-key")

            # Verify Anthropic was called
            mock_client.assert_called_once()
            assert mock_instance.messages.create.called
            # Check that prompt contains known canonicals
            call_args = mock_instance.messages.create.call_args
            prompt = call_args.kwargs["messages"][0]["content"]
            assert "Acme Corp" in prompt  # Should contain at least one canonical

    def test_ac54_llm_returns_none_falls_back_to_raw(self, vendor_file: pathlib.Path) -> None:
        """AC5.4: When LLM fails, fallback to vendor_raw unchanged."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("anthropic.Anthropic") as mock_client:

            mock_instance = MagicMock()
            mock_client.return_value = mock_instance
            mock_instance.messages.create.side_effect = Exception("API error")

            result = normalize("CompletelyNovelVendor", anthropic_api_key="test-key")

            assert result == "CompletelyNovelVendor"

    def test_ac54_empty_string_returns_unchanged(self, vendor_file: pathlib.Path) -> None:
        """AC5.4: Empty string input returns empty string unchanged."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            result = normalize("")
            assert result == ""

    def test_ac54_whitespace_only_returns_unchanged(self, vendor_file: pathlib.Path) -> None:
        """AC5.4: Whitespace-only input returns unchanged."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            result = normalize("   ")
            assert result == "   "

    def test_ac54_no_api_key_no_llm_fallback(self, vendor_file: pathlib.Path) -> None:
        """AC5.4: Without API key, LLM layer is skipped, returns vendor_raw."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("anthropic.Anthropic") as mock_client:

            result = normalize("UnknownVendor")  # No API key
            assert result == "UnknownVendor"
            mock_client.assert_not_called()

    def test_missing_vendors_json_returns_vendor_raw(self, tmp_path: pathlib.Path) -> None:
        """When vendors.json is missing, all layers fail, return vendor_raw."""
        nonexistent_path = tmp_path / "vendors.json"
        with patch("app.services.vendor_matcher._VENDORS_PATH", nonexistent_path):
            result = normalize("SomeVendor")
            assert result == "SomeVendor"

    def test_fuzzy_threshold_not_met_returns_vendor_raw(
        self, vendor_file: pathlib.Path
    ) -> None:
        """If fuzzy score < 88, skip to next layer."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            # A vendor name too different from any known canonical
            # should not trigger fuzzy match and should return as-is
            result = normalize("XYZ123Corporation")
            assert result == "XYZ123Corporation"

    def test_vendors_json_malformed_returns_vendor_raw(self, tmp_path: pathlib.Path) -> None:
        """If vendors.json is malformed JSON, skip to fallback."""
        bad_file = tmp_path / "vendors.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with patch("app.services.vendor_matcher._VENDORS_PATH", bad_file):
            result = normalize("TestVendor")
            assert result == "TestVendor"
