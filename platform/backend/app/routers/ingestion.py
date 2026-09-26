from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from reo_common.events import EventBus
from reo_common.model_gateway import GatewayError, get_model_gateway
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from evaluation.observability import persist_call_record
from models.canonical import (
    Asset,
    Constraint,
    DataMappingProposal,
    DocumentIntake,
    TableMappingRule,
)
from output.audit import append_audit_event

from ..deps import db_session, require_permission
from ..ingestion.document_ingest import (
    SUPPORTED_SUFFIXES as DOCUMENT_SUFFIXES,
)
from ..ingestion.document_ingest import (
    extract_document,
    extract_document_from_text,
    extract_text,
    render_pages,
)
from ..ingestion.document_ingest import (
    file_checksum as document_checksum,
)
from ..ingestion.file_ingest import (
    parse_telemetry_file,
    publish_readings,
    rows_from_file,
)
from ..ingestion.generic_table_mapper import (
    apply_mapping,
    column_signature,
    propose_mapping,
)
from ..ingestion.hackathon_dataset import (
    KNOWN_TABLE_KEYS,
    ingest_reference_rows,
    normalize_table_key,
)
from ..ingestion.quarantine import fetch_quarantined_file, quarantine_raw_file

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
log = logging.getLogger("api.routers.ingestion")

_bus: EventBus | None = None


def _get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


class FileIngestResponse(BaseModel):
    lineage_id: str | None = None
    rows_queued: int
    filename: str
    detail: dict | None = None  # set when the filename matched a recognized reference-dataset table or a mapping rule/proposal was involved
    proposal_id: str | None = None  # set when this upload produced a new proposal awaiting human review, instead of ingesting anything yet


@router.post("/files", response_model=FileIngestResponse)
async def upload_telemetry_file(
    file: UploadFile, ctx: AuthContext = Depends(require_permission("ingest:files")), db: Session = Depends(db_session)
) -> FileIngestResponse:
    # Document Intake uploads (this endpoint and /documents below) ingest
    # real data exactly as documented, but deliberately do NOT call
    # mark_started_if_idle() — by explicit request, only an active database/
    # data_table connector in Connector Studio gates/auto-starts the
    # optimizer (see routers/operations.py's module docstring).
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file exceeds 100MB limit")
    filename = file.filename or "upload"

    # A filename matching one of the reference dataset's own 8 files (e.g.
    # 01_customer_demographics.csv) routes through the same canonical
    # Asset-telemetry/Customer mapping Connector Studio's data_table/database
    # ingestion uses (backend/app/routers/connectors.py) instead of the
    # generic asset_id/metric/event_time/value/unit shape below, which none
    # of those 8 files are actually shaped like.
    table_key = normalize_table_key(filename)
    if table_key in KNOWN_TABLE_KEYS:
        try:
            rows = rows_from_file(filename, content)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        result = ingest_reference_rows(db, ctx.tenant_id, table_key, rows, source_label=f"file:{filename}")
        rows_queued = result.get("count", 0)
        db.commit()
        return FileIngestResponse(lineage_id=result.get("lineage_id"), rows_queued=rows_queued, filename=filename, detail=result)

    try:
        readings = parse_telemetry_file(filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if readings:
        lineage_id = publish_readings(_get_bus(), ctx.tenant_id, readings)
        db.commit()
        return FileIngestResponse(lineage_id=lineage_id, rows_queued=len(readings), filename=filename)

    # Doesn't match the fixed asset_id/metric/event_time/value/unit shape
    # either. Rather than erroring outright, try the generic column-mapping
    # agent (backend/app/ingestion/generic_table_mapper.py) — an approved
    # mapping for this exact column signature reuses that decision directly
    # with no model call; otherwise the agent proposes one for a human to
    # review before anything is actually ingested.
    try:
        rows = rows_from_file(filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file has no rows")

    headers = list(rows[0].keys())
    sig = column_signature(headers)
    rule = db.execute(
        select(TableMappingRule).where(TableMappingRule.tenant_id == ctx.tenant_id, TableMappingRule.column_signature == sig)
    ).scalar_one_or_none()
    if rule is not None:
        result = apply_mapping(db, ctx.tenant_id, rule.file_kind, rule.column_roles, rows, source_label=f"file:{filename}")
        rule.times_reused += 1
        rows_queued = result.get("count", 0)
        db.commit()
        return FileIngestResponse(lineage_id=result.get("lineage_id"), rows_queued=rows_queued, filename=filename, detail={**result, "mapping_rule_reused": True})

    assets = db.execute(select(Asset).where(Asset.tenant_id == ctx.tenant_id)).scalars().all()
    asset_context = [{"name": a.name, "asset_type": a.asset_type, "id": a.id} for a in assets]
    gateway = get_model_gateway()
    gateway.on_call_record = lambda record: persist_call_record(db, record)
    try:
        proposal_result = propose_mapping(
            gateway, tenant_id=ctx.tenant_id, correlation_id=f"map:{filename}", filename=filename,
            headers=headers, sample_rows=rows[:5], asset_context=asset_context,
        )
    except GatewayError as exc:
        db.commit()  # keep the failed-call observability row
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"could not determine how to map this file: {exc}") from exc

    if proposal_result.file_kind == "unrecognized" or not proposal_result.columns:
        db.commit()  # keep the observability row for the attempt
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"file structure not recognized: {proposal_result.reasoning}",
        )

    quarantine_key = quarantine_raw_file("mapping-proposal", content, filename=filename, reason="awaiting mapping review")
    column_roles = {c.column: c.role for c in proposal_result.columns}
    proposal = DataMappingProposal(
        tenant_id=ctx.tenant_id, filename=filename, column_signature=sig, file_kind=proposal_result.file_kind,
        confidence=proposal_result.confidence, reasoning=proposal_result.reasoning, column_roles=column_roles,
        sample_preview={"headers": headers, "rows": rows[:3]}, quarantine_key=quarantine_key,
        status="proposed", created_by=ctx.user_id,
    )
    db.add(proposal)
    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="data_mapping.proposed",
        payload={"proposal_id": proposal.id, "filename": filename, "file_kind": proposal_result.file_kind, "confidence": proposal_result.confidence},
    )
    db.commit()
    return FileIngestResponse(
        rows_queued=0, filename=filename, proposal_id=proposal.id,
        detail={"status": "proposed", "file_kind": proposal_result.file_kind, "confidence": proposal_result.confidence, "reasoning": proposal_result.reasoning},
    )


# ---------------------------------------------------------------------------
# Generic data-mapping proposal review (evidence only — see
# generic_table_mapper.py's module docstring). A human approves or rejects
# what the agent proposed above before any of it is actually ingested.
# ---------------------------------------------------------------------------


class MappingProposalResponse(BaseModel):
    id: str
    filename: str
    file_kind: str
    confidence: float
    reasoning: str
    column_roles: dict
    sample_preview: dict
    status: str
    created_at: str

    @classmethod
    def from_row(cls, row: DataMappingProposal) -> MappingProposalResponse:
        return cls(
            id=row.id, filename=row.filename, file_kind=row.file_kind, confidence=row.confidence,
            reasoning=row.reasoning, column_roles=row.column_roles, sample_preview=row.sample_preview,
            status=row.status, created_at=row.created_at.isoformat(),
        )


@router.get("/mapping-proposals", response_model=list[MappingProposalResponse])
def list_mapping_proposals(
    ctx: AuthContext = Depends(require_permission("ingest:files")), db: Session = Depends(db_session)
) -> list[MappingProposalResponse]:
    rows = db.execute(select(DataMappingProposal).order_by(DataMappingProposal.created_at.desc()).limit(100)).scalars().all()
    return [MappingProposalResponse.from_row(r) for r in rows]


@router.post("/mapping-proposals/{proposal_id}/approve", response_model=FileIngestResponse)
def approve_mapping_proposal(
    proposal_id: str, ctx: AuthContext = Depends(require_permission("ingest:files")), db: Session = Depends(db_session)
) -> FileIngestResponse:
    proposal = db.execute(select(DataMappingProposal).where(DataMappingProposal.id == proposal_id)).scalar_one_or_none()
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal not found")
    if proposal.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"proposal already {proposal.status}")

    try:
        content = fetch_quarantined_file(proposal.quarantine_key)
        rows = rows_from_file(proposal.filename, content)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not re-fetch the original file: {exc}") from exc

    result = apply_mapping(db, ctx.tenant_id, proposal.file_kind, proposal.column_roles, rows, source_label=f"mapping:{proposal.filename}")

    rule = db.execute(
        select(TableMappingRule).where(TableMappingRule.tenant_id == ctx.tenant_id, TableMappingRule.column_signature == proposal.column_signature)
    ).scalar_one_or_none()
    if rule is None:
        db.add(TableMappingRule(
            tenant_id=ctx.tenant_id, column_signature=proposal.column_signature, file_kind=proposal.file_kind,
            column_roles=proposal.column_roles, sample_filename=proposal.filename, created_by=ctx.user_id,
        ))

    proposal.status = "applied"
    rows_queued = result.get("count", 0)
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="data_mapping.applied",
        payload={"proposal_id": proposal.id, "filename": proposal.filename, "rows_queued": rows_queued},
    )
    db.commit()
    return FileIngestResponse(lineage_id=result.get("lineage_id"), rows_queued=rows_queued, filename=proposal.filename, detail=result)


@router.post("/mapping-proposals/{proposal_id}/reject", response_model=MappingProposalResponse)
def reject_mapping_proposal(
    proposal_id: str, ctx: AuthContext = Depends(require_permission("ingest:files")), db: Session = Depends(db_session)
) -> MappingProposalResponse:
    proposal = db.execute(select(DataMappingProposal).where(DataMappingProposal.id == proposal_id)).scalar_one_or_none()
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal not found")
    if proposal.status != "proposed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"proposal already {proposal.status}")
    proposal.status = "rejected"
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="data_mapping.rejected", payload={"proposal_id": proposal.id, "filename": proposal.filename},
    )
    db.commit()
    return MappingProposalResponse.from_row(proposal)


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
    def from_row(cls, row: DocumentIntake) -> DocumentIntakeResponse:
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

    is_docx = Path(filename).suffix.lower() == ".docx"
    page_count = 0
    try:
        if is_docx:
            text = extract_text(filename, content)
        else:
            pages = render_pages(filename, content)
            page_count = len(pages)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception:
        log.exception("failed to render document %s", filename)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "could not render document pages — file may be corrupt")

    gateway = get_model_gateway()
    gateway.on_call_record = lambda record: persist_call_record(db, record)
    try:
        if is_docx:
            extraction = extract_document_from_text(
                gateway, tenant_id=ctx.tenant_id, correlation_id=f"doc:{filename}", filename=filename, text=text,
            )
        else:
            extraction = extract_document(
                gateway, tenant_id=ctx.tenant_id, correlation_id=f"doc:{filename}", filename=filename, pages=pages,
            )
    except GatewayError as exc:
        db.commit()  # keep the failed-call observability row even though the upload itself is rejected
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"document could not be reliably extracted: {exc}") from exc

    row = DocumentIntake(
        tenant_id=ctx.tenant_id, filename=filename, content_type=file.content_type or "application/octet-stream",
        checksum=document_checksum(content), page_count=page_count, document_type=extraction.document_type,
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
