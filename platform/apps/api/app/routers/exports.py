from __future__ import annotations

from datetime import datetime, timedelta, timezone

import boto3
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.audit import append_audit_event
from reo_common.config import get_settings
from reo_common.events import CloudEvent, EventBus
from reo_common.models import ExportJob
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/exports", tags=["exports"])
settings = get_settings()
STREAM_EXPORT_REQUESTED = "reo.export.requested"

_bus: EventBus | None = None


def _get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


class ExportRequest(BaseModel):
    status: str | None = None
    limit: int = 500


class ExportJobResponse(BaseModel):
    id: str
    status: str
    row_count: int | None
    checksum_sha256: str | None
    created_at: str
    expires_at: str | None
    download_url: str | None = None


def _presigned_url(object_key: str) -> str:
    # Signed with the same credentials either way; only the *host* in the
    # generated URL differs, and that host must be one the caller (a
    # browser, not this container) can actually reach.
    public_endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url
    client = boto3.client(
        "s3", endpoint_url=public_endpoint, aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key, region_name=settings.s3_region,
    )
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": settings.s3_bucket_exports, "Key": object_key}, ExpiresIn=3600
    )


@router.post("", response_model=ExportJobResponse)
def request_export(
    body: ExportRequest, ctx: AuthContext = Depends(require_permission("export:evidence")), db: Session = Depends(db_session)
) -> ExportJobResponse:
    job = ExportJob(
        tenant_id=ctx.tenant_id, requested_by=ctx.user_id,
        filters={"status": body.status, "limit": body.limit}, status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(job)
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="export.requested", payload={"export_job_id": job.id, "filters": job.filters})
    db.commit()

    _get_bus().publish(STREAM_EXPORT_REQUESTED, CloudEvent(
        type="reo.export.requested", source="api", tenant_id=ctx.tenant_id,
        data={"export_job_id": job.id},
    ))
    return ExportJobResponse(id=job.id, status=job.status, row_count=None, checksum_sha256=None, created_at=job.created_at.isoformat(), expires_at=job.expires_at.isoformat())


@router.get("/{export_id}", response_model=ExportJobResponse)
def get_export(
    export_id: str, ctx: AuthContext = Depends(require_permission("export:evidence")), db: Session = Depends(db_session)
) -> ExportJobResponse:
    job = db.execute(select(ExportJob).where(ExportJob.id == export_id)).scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "export job not found")

    download_url = None
    if job.status == "complete" and job.object_key:
        download_url = _presigned_url(job.object_key)
        append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                            event_type="export.downloaded", payload={"export_job_id": job.id})
        db.commit()

    return ExportJobResponse(
        id=job.id, status=job.status, row_count=job.row_count, checksum_sha256=job.checksum_sha256,
        created_at=job.created_at.isoformat(), expires_at=job.expires_at.isoformat() if job.expires_at else None,
        download_url=download_url,
    )
