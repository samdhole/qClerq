from __future__ import annotations

import time

import httpx

_BASE_URL = "https://api.cloud.llamaindex.ai"
_POLL_INTERVAL_S = 2
_MAX_POLLS = 60


def parse_pdf(pdf_bytes: bytes, filename: str, api_key: str) -> str:
    """Upload PDF to LlamaParse v2 and return extracted plain text.

    Raises RuntimeError on non-200 responses or job failure, so callers can
    fall back to parser_pdfco.
    """
    headers = {"Authorization": f"Bearer {api_key}"}

    with httpx.Client(base_url=_BASE_URL, headers=headers, timeout=30) as client:
        upload_resp = client.post(
            "/api/v2/parse/upload",
            files={"file": (filename, pdf_bytes, "application/pdf")},
            data={"configuration": '{"tier": "agentic", "version": "latest"}'},
        )
        if upload_resp.status_code != 200:
            raise RuntimeError(
                f"llamaparse upload failed: {upload_resp.status_code} {upload_resp.text}"
            )
        job_id = upload_resp.json()["id"]

        # v2 API: upload starts the parse job; poll the same job ID for results
        for _ in range(_MAX_POLLS):
            result_resp = client.get(f"/api/v2/parse/{job_id}?expand=text_full")
            if result_resp.status_code != 200:
                raise RuntimeError(
                    f"llamaparse poll failed: {result_resp.status_code} {result_resp.text}"
                )
            data = result_resp.json()
            status = data.get("job", {}).get("status", "")
            if status == "COMPLETED":
                return data.get("text_full", "") or ""
            if status in ("FAILED", "ERROR"):
                raise RuntimeError(f"llamaparse job failed with status: {status}")
            time.sleep(_POLL_INTERVAL_S)

        raise RuntimeError("llamaparse job timed out after polling")
