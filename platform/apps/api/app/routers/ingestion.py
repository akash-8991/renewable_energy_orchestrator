from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from reo_common.audit import append_audit_event
from reo_common.events import EventBus
from reo_common.model_gateway import GatewayError, get_model_gateway
from reo_common.models import Asset, Constraint, DocumentIntake
from reo_common.observability import persist_call_record
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission
from ..ingestion.document_ingest import (
    SUPPORTED_SUFFIXES as DOCUMENT_SUFFIXES,
    extract_document,
    file_checksum as document_checksum,
    render_pages,
)
from ..ingestion.file_ingest import parse_telemetry_file, publish_readings

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
log = logging.getLogger("api.routers.ingestion")

_bus: EventBus | None = None


def _get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


class FileIngestResponse(BaseModel):
    lineage_id: str
    rows_queued: int
    filename: str


@router.post("/files", response_model=FileIngestResponse)
async def upload_telemetry_file(
    file: UploadFile, ctx: AuthContext = Depends(require_permission("ingest:files"))
) -> FileIngestResponse:
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file exceeds 25MB limit")
    try:
        readings = parse_telemetry_file(file.filename or "upload", content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if not readings:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid rows found (expected columns: asset_id, metric, event_time, value, unit)")

    lineage_id = publish_readings(_get_bus(), ctx.tenant_id, readings)
    return FileIngestResponse(lineage_id=lineage_id, rows_queued=len(readings), filename=file.filename or "upload")


# ---------------------------------------------------------------------------
# Multimodal document ingestion (D3: PDFs/images — maintenance notices, storm
# alerts, inspection reports — that CSV/JSON/XLSX telemetry can't express).
# ---------------------------------------------------------------------------


class DocumentIntakeResponse(BaseModel):
    id: str
    filename: str
    document_type: str
    summary: str
    affected_asset_refs: list[str]
    effective_from: str | None
    effective_to: str | None
    severity: str
    capacity_impact_pct: float | None
    confidence: float
    raw_excerpt: str
    status: str
    constraint_id: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: DocumentIntake) -> "DocumentIntakeResponse":
        return cls(
            id=row.id, filename=row.filename, document_type=row.document_type, summary=row.summary,
            affected_asset_refs=row.affected_asset_refs, effective_from=row.effective_from.isoformat() if row.effective_from else None,
            effective_to=row.effective_to.isoformat() if row.effective_to else None, severity=row.severity,
            capacity_impact_pct=row.capacity_impact_pct, confidence=row.confidence, raw_excerpt=row.raw_excerpt,
            status=row.status, constraint_id=row.constraint_id, created_at=row.created_at.isoformat(),
        )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/documents", response_model=DocumentIntakeResponse)
async def upload_document(
    file: UploadFile,
    ctx: AuthContext = Depends(require_permission("ingest:files")),
    db: Session = Depends(db_session),
) -> DocumentIntakeResponse:
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "document exceeds 15MB limit")
    filename = file.filename or "upload"
    if Path(filename).suffix.lower() not in DOCUMENT_SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported document type (supported: {sorted(DOCUMENT_SUFFIXES)})")

    try:
        pages = render_pages(filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception:
        log.exception("failed to render document %s", filename)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "could not render document pages — file may be corrupt")

    gateway = get_model_gateway()
    gateway.on_call_record = lambda record: persist_call_record(db, record)
    try:
        extraction = extract_document(
            gateway, tenant_id=ctx.tenant_id, correlation_id=f"doc:{filename}", filename=filename, pages=pages,
        )
    except GatewayError as exc:
        db.commit()  # keep the failed-call observability row even though the upload itself is rejected
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"document could not be reliably extracted: {exc}") from exc

    row = DocumentIntake(
        tenant_id=ctx.tenant_id, filename=filename, content_type=file.content_type or "application/octet-stream",
        checksum=document_checksum(content), page_count=len(pages), document_type=extraction.document_type,
        summary=extraction.summary, affected_asset_refs=extraction.affected_asset_refs,
        effective_from=_parse_iso(extraction.effective_from), effective_to=_parse_iso(extraction.effective_to),
        severity=extraction.severity, capacity_impact_pct=extraction.capacity_impact_pct,
        confidence=extraction.confidence, raw_excerpt=extraction.raw_excerpt,
        model_provider=get_model_gateway().provider_name, uploaded_by=ctx.email,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="document.ingested",
        payload={"document_id": row.id, "filename": filename, "document_type": row.document_type, "checksum": row.checksum},
    )
    db.commit()
    return DocumentIntakeResponse.from_row(row)


@router.get("/documents", response_model=list[DocumentIntakeResponse])
def list_documents(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)
) -> list[DocumentIntakeResponse]:
    rows = db.execute(select(DocumentIntake).order_by(DocumentIntake.created_at.desc()).limit(100)).scalars().all()
    return [DocumentIntakeResponse.from_row(r) for r in rows]


class ApplyConstraintRequest(BaseModel):
    asset_id: str
    max_capacity_pct: float = 0.0  # 0 = fully unavailable; 40 = derated to 40% of rated capacity
    effective_from: str | None = None  # falls back to the extracted value if omitted
    effective_to: str | None = None


@router.post("/documents/{document_id}/apply-constraint", response_model=DocumentIntakeResponse)
def apply_document_constraint(
    document_id: str,
    body: ApplyConstraintRequest,
    ctx: AuthContext = Depends(require_permission("manage:constraints")),
    db: Session = Depends(db_session),
) -> DocumentIntakeResponse:
    if not body.asset_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "asset_id is required")

    row = db.execute(select(DocumentIntake).where(DocumentIntake.id == document_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if row.status != "extracted":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"document already {row.status}")

    try:
        asset = db.execute(select(Asset).where(Asset.id == body.asset_id, Asset.tenant_id == ctx.tenant_id)).scalar_one_or_none()
    except DataError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "asset_id is not a valid identifier")
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if asset.asset_type not in ("solar", "wind"):
        # The optimizer only consumes a per-step generation cap for solar/wind
        # (cycle.py derates their forecast series) — batteries and the grid
        # interconnection have their own, structurally different constraint
        # handling. Accepting this here for other asset types would persist
        # a Constraint row the optimizer never reads, which is worse than
        # refusing: a silently inert "fix" that looks applied in the UI.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "document-derived constraints are currently only wired into the optimizer for solar/wind generation assets",
        )

    effective_from = _parse_iso(body.effective_from) or row.effective_from
    effective_to = _parse_iso(body.effective_to) or row.effective_to
    if effective_from is None or effective_to is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "effective_from and effective_to are required (document did not state one)")

    constraint = Constraint(
        tenant_id=ctx.tenant_id, scope=f"asset:{asset.id}", constraint_type="capacity_derate_from_document",
        expression={"max_capacity_pct": body.max_capacity_pct, "document_id": row.id},
        is_hard=True, limit_value=body.max_capacity_pct,
        effective_from=effective_from, effective_to=effective_to,
        source=f"document_intake:{row.id}",
    )
    db.add(constraint)
    db.flush()

    row.status = "applied"
    row.constraint_id = constraint.id
    row.applied_by = ctx.email
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="document.applied_constraint",
        payload={"document_id": row.id, "constraint_id": constraint.id, "asset_id": asset.id, "max_capacity_pct": body.max_capacity_pct},
    )
    db.commit()
    return DocumentIntakeResponse.from_row(row)
