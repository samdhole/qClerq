from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)

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
    except Exception as e:
        logger.warning(f"Dedupe check failed (best-effort): {e}", exc_info=True)

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
        except Exception as e:
            logger.warning(f"Failed to write exceptions to Sheets (best-effort): {e}", exc_info=True)

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
    """Sync to all three targets. Sheets row written first; backfilled after QB + Jobber."""
    from app.services import sheets_sync, quickbooks_sync, jobber_sync

    sheets_row_id: str | None = None
    qb_bill_id: str | None = None
    jobber_expense_id: str | None = None
    sync_status: dict[str, str] = {}

    # 1. Write Sheets row first — establishes the audit record before any sync attempt.
    try:
        sheets_row_id = await asyncio.to_thread(
            sheets_sync.write_invoice_row,
            settings.sheet_id,
            req,
            None,
            None,
            sync_status,
            settings.google_service_account_json,
        )
        sync_status["sheets"] = "ok"
    except Exception as e:
        logger.error(f"Sheets write failed: {e}", exc_info=True)
        sync_status["sheets"] = "failed"

    # 2. QB sync — failure does not block Jobber (AC4.4).
    try:
        qb_result = await quickbooks_sync.sync(req, settings)
        qb_bill_id = qb_result.qb_bill_id
        sync_status["quickbooks"] = qb_result.sync_status.get("quickbooks", "ok")
    except Exception as e:
        logger.error(f"QuickBooks sync failed: {e}", exc_info=True)
        sync_status["quickbooks"] = "failed"

    # 3. Jobber sync — failure does not affect QB or Sheets (AC4.5).
    try:
        jobber_result = await jobber_sync.sync(req, settings)
        jobber_expense_id = jobber_result.jobber_expense_id
        sync_status["jobber"] = jobber_result.sync_status.get("jobber", "ok")
    except Exception as e:
        logger.error(f"Jobber sync failed: {e}", exc_info=True)
        sync_status["jobber"] = "failed"

    # 4. Backfill Sheets row with final IDs and sync_status (AC4.6, AC6.1).
    if sheets_row_id is not None:
        try:
            await asyncio.to_thread(
                sheets_sync.update_sync_status,
                settings.sheet_id,
                int(sheets_row_id),
                sync_status,
                qb_bill_id,
                jobber_expense_id,
                settings.google_service_account_json,
            )
        except Exception as e:
            logger.warning(f"Sheets backfill failed (best-effort): {e}", exc_info=True)

    return SyncResult(
        sheets_row_id=sheets_row_id,
        qb_bill_id=qb_bill_id,
        jobber_expense_id=jobber_expense_id,
        sync_status=sync_status,
    )


@router.get("/report/weekly")
async def weekly_report(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WeeklySummary:
    from app.services import report_generator
    return await report_generator.generate(settings)
