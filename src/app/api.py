from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.schemas.invoice import (
    InvoiceExtracted,
    SyncRequest,
    SyncResult,
    ValidationResult,
    WeeklySummary,
)
from app.services import extractor_gemini, validator as inv_validator
from app.services.dedupe import (
    KnownInvoice,
    check_duplicate,
    check_semantic_duplicate,
    compute_hash,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = ...,  # type: ignore[assignment]
) -> None:
    """Dependency that enforces X-API-Key header on all protected routes (H-2)."""
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


_AuthDep = Annotated[None, Depends(_verify_api_key)]


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/extract")
async def extract_invoice(
    file: UploadFile,
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceExtracted:
    # Content-type pre-check; magic bytes validated after read
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail=f"Only PDF files accepted, got: {file.content_type}")

    pdf_bytes = await file.read()

    # File size guard (M-3)
    if len(pdf_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(pdf_bytes)} bytes); max {settings.max_upload_bytes}",
        )

    # PDF magic bytes check — rejects non-PDF binaries regardless of content-type (L-5)
    if pdf_bytes[:4] != b"%PDF":
        raise HTTPException(status_code=415, detail="File does not appear to be a valid PDF")

    file_hash = compute_hash(pdf_bytes)
    file_name = file.filename or "invoice.pdf"

    result = extractor_gemini.parse_and_extract(
        pdf_bytes=pdf_bytes,
        file_hash=file_hash,
        file_name=file_name,
        gemini_api_key=settings.gemini_api_key,
        gemini_model=settings.gemini_model,
    )
    if result is None:
        raise HTTPException(status_code=422, detail="Extraction failed — could not parse PDF")

    from app.services.vendor_matcher import normalize
    result.vendor_normalized = normalize(result.vendor_raw, settings.gemini_api_key)

    # Dedupe check: hash + semantic (H-1)
    try:
        from app.services.sheets_sync import get_known_invoice_data
        known_hashes, known_invoices = await asyncio.to_thread(
            get_known_invoice_data, settings.sheet_id, settings.google_service_account_json
        )
        hash_risk = check_duplicate(file_hash, known_hashes)
        semantic_risk = check_semantic_duplicate(
            vendor_normalized=result.vendor_normalized,
            invoice_number=result.invoice_number,
            total=result.total,
            invoice_date=result.invoice_date,
            known_invoices=known_invoices,
        )
        # Take the worse of the two signals
        _risk_order = {"none": 0, "possible": 1, "likely": 2}
        result.duplicate_risk = max(hash_risk, semantic_risk, key=lambda r: _risk_order[r])
    except Exception as e:
        logger.warning(f"Dedupe check failed (best-effort): {e}", exc_info=True)
        result.duplicate_risk = "possible"
        result.warnings.append("Dedupe check unavailable — treating as possible duplicate")

    return result


@router.post("/validate")
async def validate_invoice(
    invoice: InvoiceExtracted,
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ValidationResult:
    validation = inv_validator.validate(
        invoice,
        settings.approval_tier_1_max,
        settings.approval_tier_2_max,
        dedupe_result=invoice.duplicate_risk,
    )

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


def _check_approved_by(approved_by: str, settings: Settings) -> None:
    """Validate approved_by against configured allowlist (H-3)."""
    if not approved_by:
        raise HTTPException(status_code=422, detail="approved_by is required to sync")
    if settings.valid_approvers and approved_by not in settings.valid_approvers:
        raise HTTPException(
            status_code=422,
            detail=f"approved_by '{approved_by}' is not in the configured approver allowlist",
        )


async def _run_sync(req: SyncRequest, settings: Settings) -> SyncResult:
    """Execute the three-target sync. Called by both approval_callback and sync_all."""
    from app.services import sheets_sync, quickbooks_sync, jobber_sync

    sheets_row_id: str | None = None
    qb_bill_id: str | None = None
    jobber_expense_id: str | None = None
    # Write explicit pending status — not empty dict — so partial failures are visible (H-4)
    sync_status: dict[str, str] = {
        "sheets": "pending",
        "quickbooks": "pending",
        "jobber": "pending",
    }

    # 1. Write Sheets row first with pending status to establish audit record
    try:
        sheets_row_id = await asyncio.to_thread(
            sheets_sync.write_invoice_row,
            settings.sheet_id,
            req,
            None,
            None,
            sync_status,
            settings.google_service_account_json,
            "approved",
        )
        sync_status["sheets"] = "ok"
    except Exception as e:
        logger.error(f"Sheets write failed: {e}", exc_info=True)
        sync_status["sheets"] = "failed"

    # 2. QB sync — failure does not block Jobber (AC4.4)
    try:
        qb_result = await quickbooks_sync.sync(req, settings)
        qb_bill_id = qb_result.qb_bill_id
        sync_status["quickbooks"] = qb_result.sync_status.get("quickbooks", "ok")
    except Exception as e:
        logger.error(f"QuickBooks sync failed: {e}", exc_info=True)
        sync_status["quickbooks"] = "failed"

    # 3. Jobber sync — failure does not affect QB or Sheets (AC4.5)
    try:
        jobber_result = await jobber_sync.sync(req, settings)
        jobber_expense_id = jobber_result.jobber_expense_id
        sync_status["jobber"] = jobber_result.sync_status.get("jobber", "ok")
    except Exception as e:
        logger.error(f"Jobber sync failed: {e}", exc_info=True)
        sync_status["jobber"] = "failed"

    # 4. Backfill Sheets row with final IDs and sync_status (AC4.6, AC6.1)
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


@router.post("/approval-callback")
async def approval_callback(
    req: SyncRequest,
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Receive n8n sendAndWait approval payload and trigger full sync (AC3.4)."""
    _check_approved_by(req.approved_by, settings)
    if req.approval_tier == "auto":
        raise HTTPException(status_code=422, detail="auto-tier invoices must use /sync directly")
    return await _run_sync(req, settings)


@router.post("/sync")
async def sync_all(
    req: SyncRequest,
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Sync approved auto-tier invoices to all three targets (AC4.x)."""
    _check_approved_by(req.approved_by, settings)
    # /sync is for auto-tier only — non-auto invoices must go through /approval-callback (M-5)
    if req.approval_tier != "auto":
        raise HTTPException(
            status_code=422,
            detail="Non-auto-tier invoices must be approved via /approval-callback",
        )
    return await _run_sync(req, settings)


@router.get("/report/weekly")
async def weekly_report(
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> WeeklySummary:
    from app.services import report_generator
    return await report_generator.generate(settings)
