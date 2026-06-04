"""Tests for invoice extraction pipeline.

Verifies:
- AC2.1: Valid PDF returns InvoiceExtracted with all required fields
- AC2.2: confidence_overall reflects readability
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
from app.services.extractor_gemini import extract, parse_and_extract
from app.services.vendor_matcher import normalize


def make_gemini_tool_response(data: dict) -> MagicMock:
    """Create a mocked Gemini function_call response."""
    fc = MagicMock()
    fc.args = data
    part = MagicMock()
    part.function_call = fc
    candidate = MagicMock()
    candidate.content.parts = [part]
    response = MagicMock()
    response.candidates = [candidate]
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


_FAKE_PDF = b"%PDF-1.4 fake pdf bytes"


class TestExtract:
    """Test extract() function — Gemini receives PDF bytes directly."""

    def test_ac21_valid_pdf_returns_invoice_extracted(self) -> None:
        """AC2.1: Valid PDF bytes return InvoiceExtracted with all required fields."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_client = MagicMock()
            mock_model_cls.return_value = mock_client
            mock_client.models.generate_content.return_value = make_gemini_tool_response(
                make_valid_tool_input()
            )

            result = extract(
                pdf_bytes=_FAKE_PDF,
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
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            data = make_valid_tool_input()
            data["confidence_overall"] = 0.95
            mock_instance.models.generate_content.return_value = make_gemini_tool_response(data)

            result = extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash1",
                file_name="clean.pdf",
                api_key="test-key",
            )

            assert result is not None
            assert result.confidence_overall >= 0.7

    def test_ac22_low_confidence_for_poor_quality(self) -> None:
        """AC2.2: Scanned/poor quality → confidence_overall < 0.7."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            data = make_valid_tool_input()
            data["confidence_overall"] = 0.4
            mock_instance.models.generate_content.return_value = make_gemini_tool_response(data)

            result = extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash2",
                file_name="scanned.pdf",
                api_key="test-key",
            )

            assert result is not None
            assert result.confidence_overall < 0.7

    def test_extract_returns_none_on_client_exception(self) -> None:
        """extract() returns None on any client exception."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_instance.models.generate_content.side_effect = Exception("API error")

            result = extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash3",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None

    def test_extract_returns_none_on_missing_function_call(self) -> None:
        """extract() returns None when response has no function_call block."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            response = MagicMock()
            response.candidates = []
            mock_instance.models.generate_content.return_value = response

            result = extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash4",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None

    def test_extract_returns_none_on_validation_failure(self) -> None:
        """extract() returns None when Pydantic validation fails."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            bad_data = {"vendor_raw": "test"}  # Missing required fields
            mock_instance.models.generate_content.return_value = make_gemini_tool_response(bad_data)

            result = extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash6",
                file_name="test.pdf",
                api_key="test-key",
            )

            assert result is None


class TestParseAndExtract:
    """Test parse_and_extract() — thin wrapper around extract()."""

    def test_parse_and_extract_success(self) -> None:
        """parse_and_extract() delegates to extract() and returns result."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_instance.models.generate_content.return_value = make_gemini_tool_response(
                make_valid_tool_input()
            )

            result = parse_and_extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash7",
                file_name="test.pdf",
                gemini_api_key="gemini-key",
            )

            assert result is not None
            assert result.vendor_raw == "Acme Corp"

    def test_parse_and_extract_gemini_exception_returns_none(self) -> None:
        """When Gemini raises, parse_and_extract() returns None."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_instance.models.generate_content.side_effect = Exception("API error")

            result = parse_and_extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash8",
                file_name="test.pdf",
                gemini_api_key="gemini-key",
            )

            assert result is None

    def test_parse_and_extract_extraction_failure_returns_none(self) -> None:
        """When extraction validation fails, return None."""
        with patch("google.genai.Client") as mock_model_cls:
            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_instance.models.generate_content.return_value = make_gemini_tool_response(
                {"vendor_raw": "incomplete"}  # Missing required fields
            )

            result = parse_and_extract(
                pdf_bytes=_FAKE_PDF,
                file_hash="hash11",
                file_name="test.pdf",
                gemini_api_key="gemini-key",
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
            result = normalize("WW Grainger")
            assert result == "W.W. Grainger"

    def test_ac52_fuzzy_match_with_suffix_variation(self, vendor_file: pathlib.Path) -> None:
        """AC5.2: Fuzzy match works with variant suffix (Lowe's Company vs Lowe's)."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file):
            result = normalize("Lowe's Company")
            assert result == "Lowe's"

    def test_ac53_novel_vendor_llm_matches_existing_canonical(self, vendor_file: pathlib.Path) -> None:
        """AC5.3: LLM fallback matches a known canonical and promotes the alias to vendors.json."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("app.services.vendor_matcher._vendor_cache", None), \
             patch("google.genai.Client") as mock_model_cls:

            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = "Acme Corp"
            mock_instance.models.generate_content.return_value = mock_response

            result = normalize("AcmeCorporation", gemini_api_key="test-key")

            assert result == "Acme Corp"
            data = json.loads(vendor_file.read_text(encoding="utf-8"))
            assert data["AcmeCorporation"] == "Acme Corp"

    def test_ac53_novel_vendor_llm_new_name_falls_back_to_raw(self, vendor_file: pathlib.Path) -> None:
        """AC5.3: LLM returning a name not in known canonicals is rejected; falls back to raw (C-2)."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("app.services.vendor_matcher._vendor_cache", None), \
             patch("google.genai.Client") as mock_model_cls:

            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = "Brand New Vendor"
            mock_instance.models.generate_content.return_value = mock_response

            result = normalize("UnknownVendor XYZ", gemini_api_key="test-key")

            assert result == "UnknownVendor XYZ"

    def test_ac53_llm_called_with_known_canonicals(self, vendor_file: pathlib.Path) -> None:
        """AC5.3: LLM receives the list of known canonical names."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("google.genai.Client") as mock_model_cls:

            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_response = MagicMock()
            mock_response.text = "Novel Vendor"
            mock_instance.models.generate_content.return_value = mock_response

            normalize("ForeignVendor", gemini_api_key="test-key")

            mock_model_cls.assert_called_once()
            assert mock_instance.models.generate_content.called
            call_args = mock_instance.models.generate_content.call_args
            prompt = call_args.kwargs.get("contents") or call_args.args[0]
            assert "Acme Corp" in prompt

    def test_ac54_llm_returns_none_falls_back_to_raw(self, vendor_file: pathlib.Path) -> None:
        """AC5.4: When LLM fails, fallback to vendor_raw unchanged."""
        with patch("app.services.vendor_matcher._VENDORS_PATH", vendor_file), \
             patch("google.genai.Client") as mock_model_cls:

            mock_instance = MagicMock()
            mock_model_cls.return_value = mock_instance
            mock_instance.models.generate_content.side_effect = Exception("API error")

            result = normalize("CompletelyNovelVendor", gemini_api_key="test-key")

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
             patch("google.genai.Client") as mock_model_cls:

            result = normalize("UnknownVendor")  # No API key
            assert result == "UnknownVendor"
            mock_model_cls.assert_not_called()

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
            result = normalize("XYZ123Corporation")
            assert result == "XYZ123Corporation"

    def test_vendors_json_malformed_returns_vendor_raw(self, tmp_path: pathlib.Path) -> None:
        """If vendors.json is malformed JSON, skip to fallback."""
        bad_file = tmp_path / "vendors.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with patch("app.services.vendor_matcher._VENDORS_PATH", bad_file):
            result = normalize("TestVendor")
            assert result == "TestVendor"
