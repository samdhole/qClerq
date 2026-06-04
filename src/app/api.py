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
from app.services.approval_router import route as compute_tier
from app.services.dedupe import (
    KnownInvoice,
    check_duplicate,
    check_semantic_duplicate,
    compute_hash,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_RISK_ORDER = {"none": 0, "possible": 1, "likely": 2}


def _verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = ...,  # type: ignore[assignment]
) -> None:
    """Dependency that enforces X-API-Key header on all protected routes (H-2)."""
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


_AuthDep = Annotated[None, Depends(_verify_api_key)]


async def _lookup_duplicate_risk(invoice: InvoiceExtracted, settings: Settings) -> str:
    """Worse of the file-hash and semantic dedupe signals from the Sheets ledger.

    Deterministic (no LLM). Raises on lookup failure so each caller can choose its own
    fallback: /extract fails safe to "possible"; /validate falls back to the caller value.
    """
    from app.services.sheets_sync import get_known_invoice_data

    known_hashes, known_invoices = await asyncio.to_thread(
        get_known_invoice_data, settings.sheet_id, settings.google_service_account_json
    )
    hash_risk = check_duplicate(invoice.file_hash, known_hashes)
    semantic_risk = check_semantic_duplicate(
        vendor_normalized=invoice.vendor_normalized,
        invoice_number=invoice.invoice_number,
        total=invoice.total,
        invoice_date=invoice.invoice_date,
        known_invoices=known_invoices,
    )
    return max(hash_risk, semantic_risk, key=lambda r: _RISK_ORDER[r])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/extract")
async def extract_invoice(
    file: UploadFile,
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceExtracted:
    # Content-type pre-check (L-3). application/octet-stream is intentionally allowed:
    # Drive/email clients frequently deliver legitimate PDFs as octet-stream. The magic-byte
    # check below is the AUTHORITATIVE gate — a non-PDF of any declared type is rejected there.
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

    # Dedupe check: hash + semantic (H-1). Fail safe to "possible" if the ledger is unreachable.
    try:
        result.duplicate_risk = await _lookup_duplicate_risk(result, settings)
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
    # M-2: do not trust the caller-supplied duplicate_risk as the security signal.
    # Recompute deterministically (best-effort) and take the WORSE of the two — a spoofed
    # "none" cannot hide a real duplicate, while /extract's fail-safe value (and any dedupe
    # outage) can still raise — never lower — the risk used for gating.
    recomputed_risk = "none"
    try:
        recomputed_risk = await _lookup_duplicate_risk(invoice, settings)
    except Exception:
        logger.warning("validate dedupe recompute failed; using caller value only", exc_info=True)
    dedupe_result = max(invoice.duplicate_risk, recomputed_risk, key=lambda r: _RISK_ORDER[r])

    validation = inv_validator.validate(
        invoice,
        settings.approval_tier_1_max,
        settings.approval_tier_2_max,
        dedupe_result=dedupe_result,
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
    """Validate approved_by against the configured allowlist — fail closed (H-3).

    An EMPTY allowlist means no approver is authorised for write endpoints, so we refuse
    rather than accept any non-empty string (the prior behaviour made the allowlist a
    no-op under the default config, which is what let a spoofed 'auto-approved' through).
    """
    if not approved_by:
        raise HTTPException(status_code=422, detail="approved_by is required to sync")
    if not settings.valid_approvers:
        raise HTTPException(
            status_code=403,
            detail="No approver allowlist configured (valid_approvers is empty); refusing to sync",
        )
    if approved_by not in settings.valid_approvers:
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
    # C-1 / L-4: never trust the client-declared tier. Recompute from the invoice total.
    computed_tier = compute_tier(
        req.invoice.total, settings.approval_tier_1_max, settings.approval_tier_2_max
    )
    if computed_tier == "auto":
        raise HTTPException(status_code=422, detail="auto-tier invoices must use /sync directly")
    req.approval_tier = computed_tier  # audit the server-computed tier, not the caller's claim
    return await _run_sync(req, settings)


@router.post("/sync")
async def sync_all(
    req: SyncRequest,
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncResult:
    """Sync approved auto-tier invoices to all three targets (AC4.x)."""
    # /sync is the AUTO path: invoices below tier-1 are system-approved with no human approver,
    # so the n8n auto node legitimately sends approved_by="auto-approved". The security gate
    # here is the API key + the server-side tier recompute below — NOT the human approver
    # allowlist (that belongs on /approval-callback). We still require approved_by for the audit.
    if not req.approved_by:
        raise HTTPException(status_code=422, detail="approved_by is required to sync")
    # C-1 / L-4 / M-5: recompute the tier server-side from the invoice total. A high-value
    # invoice spoofed as approval_tier="auto" (the exact C-1 bypass) is rejected here even if
    # the n8n graph or a direct caller claims auto. /sync never trusts req.approval_tier.
    computed_tier = compute_tier(
        req.invoice.total, settings.approval_tier_1_max, settings.approval_tier_2_max
    )
    if computed_tier != "auto":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invoice total routes to '{computed_tier}' tier; non-auto invoices must be "
                "approved via /approval-callback, not /sync"
            ),
        )
    req.approval_tier = "auto"
    return await _run_sync(req, settings)


@router.get("/report/weekly")
async def weekly_report(
    _auth: _AuthDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> WeeklySummary:
    from app.services import report_generator
    return await report_generator.generate(settings)
