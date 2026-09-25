from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from models.canonical import Signal
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/signals", tags=["signals"])


class SignalSummary(BaseModel):
    id: str
    correlation_id: str
    target_asset_id: str
    command_type: str
    setpoint_value: float
    unit: str
    state: str
    state_history: list
    created_at: str
    updated_at: str


@router.get("", response_model=list[SignalSummary])
def list_signals(
    limit: int = Query(100, le=1000),
    ctx: AuthContext = Depends(require_permission("read:decisions")),
    db: Session = Depends(db_session),
) -> list[SignalSummary]:
    rows = db.execute(select(Signal).order_by(Signal.created_at.desc()).limit(limit)).scalars().all()
    return [
        SignalSummary(
            id=s.id, correlation_id=s.correlation_id, target_asset_id=s.target_asset_id,
            command_type=s.command_type, setpoint_value=s.setpoint_value, unit=s.unit, state=s.state,
            state_history=s.state_history, created_at=s.created_at.isoformat(), updated_at=s.updated_at.isoformat(),
        )
        for s in rows
    ]
