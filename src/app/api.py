from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.schemas.invoice import (
    InvoiceExtracted,
    SyncRequest,
    SyncResult,
    ValidationResult,
    WeeklySummary,
)
from app.services import extractor_claude, validator as inv_validator
from app.services.dedupe import check_duplicate, compute_hash

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/extract")
async def extract_invoice(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceExtracted:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail=f"Only PDF files accepted, got: {file.content_type}")
    pdf_bytes = await file.read()
    file_hash = compute_hash(pdf_bytes)
    file_name = file.filename or "invoice.pdf"

    result = extractor_claude.parse_and_extract(
        pdf_bytes=pdf_bytes,
        file_hash=file_hash,
        file_name=file_name,
        llama_api_key=settings.llama_cloud_api_key,
        pdfco_api_key=settings.pdfco_api_key,
        anthropic_api_key=settings.anthropic_api_key,
    )
    if result is None:
        raise HTTPException(status_code=422, detail="Extraction failed — could not parse PDF")

    from app.services.vendor_matcher import normalize
    result.vendor_normalized = normalize(result.vendor_raw, settings.anthropic_api_key)

    # Dedupe check: fetch known hashes from Sheets and flag duplicates (AC1.5).
    try:
        from app.services.sheets_sync import get_known_hashes
        known = await asyncio.to_thread(
            get_known_hashes, settings.sheet_id, settings.google_service_account_json
        )
        result.duplicate_risk = check_duplicate(file_hash, known)
    except Exception:
        pass  # best-effort — do not block extraction if Sheets is unreachable

    return result


@router.post("/validate")
async def validate_invoice(
    invoice: InvoiceExtracted,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ValidationResult:
    validation = inv_validator.validate(
        invoice,
        settings.approval_tier_1_max,
        settings.approval_tier_2_max,
    )

    # Write exception rows for any issues found (AC6.2).
    if not validation.is_clean and validation.exceptions:
        try:
            from app.services.sheets_sync import write_exceptions
            await asyncio.to_thread(
                write_exceptions,
                settings.sheet_id,
                invoice,
                validation.exceptions,
                settings.google_service_account_json,
            )
        except Exception:
            pass  # best-effort — validation result is still returned to caller

    return validation


@router.post("/approval-callback")
async def approval_callback(
    req: SyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Receive n8n sendAndWait approval payload and trigger full sync (AC3.4).

    n8n sends approved_by, approval_notes, approved_at from the sendAndWait
    resume payload. This endpoint validates that approved_by is non-empty
    (the sync gating requirement per CONTEXT.md) and delegates to sync_all.
    """
    if not req.approved_by:
        raise HTTPException(status_code=422, detail="approved_by is required to sync")
    # Delegate to the combined sync flow defined in Phase 6.
    # NOTE: sync_all raises 501 until Phase 6 Task 4 replaces its body — this endpoint will also 501 until then.
    return await sync_all(req, settings)


@router.post("/sync")
async def sync_all(
    req: SyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Run all sync targets. Implementation populated in Phase 6 Task 4."""
    from app.services import sheets_sync, quickbooks_sync, jobber_sync  # noqa: F401
    raise HTTPException(status_code=501, detail="Sync not yet implemented — complete Phase 6")


@router.get("/report/weekly")
async def weekly_report(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WeeklySummary:
    from app.services import report_generator
    return await report_generator.generate(settings)
