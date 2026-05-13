from __future__ import annotations

import base64

import requests

_ENDPOINT = "https://api.pdf.co/v1/pdf/convert/to/text"


def parse_pdf(pdf_bytes: bytes, filename: str, api_key: str) -> str:
    """Extract text from PDF bytes via PDF.co API.

    Raises RuntimeError on non-200 responses or API error.
    """
    encoded = base64.b64encode(pdf_bytes).decode()

    payload = {
        "file": encoded,
        "name": filename,
        "inline": True,
        "async": False,
    }
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    resp = requests.post(_ENDPOINT, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(
            f"pdfco request failed: {resp.status_code} {resp.text}"
        )
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"pdfco api error: {data.get('message', 'unknown')}")
    return data.get("body", "") or ""
