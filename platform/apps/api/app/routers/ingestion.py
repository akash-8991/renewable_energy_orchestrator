from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from reo_common.events import EventBus
from reo_common.security import AuthContext

from ..deps import require_permission
from ..ingestion.file_ingest import parse_telemetry_file, publish_readings

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

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
