from __future__ import annotations

import logging
import pathlib
from typing import Any

import anthropic

from app.schemas.invoice import InvoiceExtracted

_PROMPT_PATH = pathlib.Path(__file__).parent.parent / "prompts" / "invoice_extraction.md"
# Pinned to Claude Sonnet 4.6 (the correct current model per project environment)
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096

_TOOL_SCHEMA: dict[str, Any] = {
    "name": "extract_invoice",
    "description": "Extract all structured fields from an invoice document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "invoice_number":          {"type": ["string", "null"]},
            "invoice_date":            {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "due_date":                {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "vendor_raw":              {"type": "string"},
            "vendor_normalized":       {"type": "string"},
            "po_number":               {"type": ["string", "null"]},
            "job_id":                  {"type": ["string", "null"]},
            "subtotal":                {"type": "number"},
            "tax":                     {"type": "number"},
            "shipping":                {"type": "number"},
            "discount":                {"type": "number"},
            "total":                   {"type": "number"},
            "currency":                {"type": "string"},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "quantity":    {"type": "number"},
                        "unit_price":  {"type": "number"},
                        "line_total":  {"type": "number"},
                        "category":    {"type": "string"},
                    },
                    "required": ["description", "quantity", "unit_price", "line_total", "category"],
                },
            },
            "payment_terms":           {"type": ["string", "null"]},
            "confidence_overall":      {"type": "number"},
            "duplicate_risk":          {"type": "string", "enum": ["none", "possible", "likely"]},
            "missing_required_fields": {"type": "array", "items": {"type": "string"}},
            "warnings":                {"type": "array", "items": {"type": "string"}},
            "file_hash":               {"type": "string"},
            "file_name":               {"type": "string"},
        },
        "required": [
            "vendor_raw", "vendor_normalized", "subtotal", "tax", "shipping",
            "discount", "total", "currency", "line_items", "confidence_overall",
            "duplicate_risk", "missing_required_fields", "warnings", "file_hash", "file_name",
        ],
    },
}


def extract(
    raw_text: str,
    file_hash: str,
    file_name: str,
    api_key: str,
) -> InvoiceExtracted | None:
    """Extract invoice fields from raw text using Claude tool_use.

    Returns None on any failure (caller handles fallback or exception routing).
    Never raises.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "extract_invoice"},
            messages=[{"role": "user", "content": f"Extract all invoice fields:\n\n{raw_text}"}],
        )
    except Exception:
        logging.exception("Claude API call failed during extraction")
        return None

    if response.stop_reason != "tool_use":
        return None

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        return None

    data: dict[str, Any] = tool_block.input
    data["file_hash"] = file_hash
    data["file_name"] = file_name

    try:
        return InvoiceExtracted.model_validate(data)
    except Exception:
        logging.exception("Pydantic validation failed for extracted invoice data")
        return None


def parse_and_extract(
    pdf_bytes: bytes,
    file_hash: str,
    file_name: str,
    llama_api_key: str,
    pdfco_api_key: str,
    anthropic_api_key: str,
) -> InvoiceExtracted | None:
    """Parse PDF and extract fields. Falls back to PDF.co if LlamaParse fails.

    Returns None if both parsers fail or extraction fails.
    """
    from app.services import parser_llamaparse, parser_pdfco

    raw_text: str | None = None

    try:
        raw_text = parser_llamaparse.parse_pdf(pdf_bytes, file_name, llama_api_key)
    except Exception:
        pass

    if not raw_text:
        try:
            raw_text = parser_pdfco.parse_pdf(pdf_bytes, file_name, pdfco_api_key)
        except Exception:
            return None

    if not raw_text:
        return None

    return extract(raw_text, file_hash, file_name, anthropic_api_key)
