from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from reo_common.models import Decision
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/decisions", tags=["decisions"])


class DecisionSummary(BaseModel):
    id: str
    decision_cycle_id: str
    version: int
    trigger: str
    status: str
    autonomy_mode: str
    confidence: float
    binding_constraints: list[str]
    risk_flags: list
    created_at: str
    expires_at: str | None


class DecisionDetail(DecisionSummary):
    plan: dict
    alternatives: list
    reasoning: dict
    trusted_snapshot_ref: str | None
    forecast_bundle_ref: str | None


@router.get("", response_model=list[DecisionSummary])
def list_decisions(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=500),
    ctx: AuthContext = Depends(require_permission("read:decisions")),
    db: Session = Depends(db_session),
) -> list[DecisionSummary]:
    stmt = select(Decision).order_by(Decision.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Decision.status == status_filter)
    rows = db.execute(stmt).scalars().all()
    return [
        DecisionSummary(
            id=d.id, decision_cycle_id=d.decision_cycle_id, version=d.version, trigger=d.trigger,
            status=d.status, autonomy_mode=d.autonomy_mode, confidence=d.confidence,
            binding_constraints=d.binding_constraints, risk_flags=d.risk_flags,
            created_at=d.created_at.isoformat(), expires_at=d.expires_at.isoformat() if d.expires_at else None,
        )
        for d in rows
    ]


@router.get("/{decision_id}", response_model=DecisionDetail)
def get_decision(
    decision_id: str,
    ctx: AuthContext = Depends(require_permission("read:decisions")),
    db: Session = Depends(db_session),
) -> DecisionDetail:
    d = db.execute(select(Decision).where(Decision.id == decision_id)).scalar_one_or_none()
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision not found")
    return DecisionDetail(
        id=d.id, decision_cycle_id=d.decision_cycle_id, version=d.version, trigger=d.trigger,
        status=d.status, autonomy_mode=d.autonomy_mode, confidence=d.confidence,
        binding_constraints=d.binding_constraints, risk_flags=d.risk_flags,
        created_at=d.created_at.isoformat(), expires_at=d.expires_at.isoformat() if d.expires_at else None,
        plan=d.plan, alternatives=d.alternatives, reasoning=d.reasoning or {},
        trusted_snapshot_ref=d.trusted_snapshot_ref, forecast_bundle_ref=d.forecast_bundle_ref,
    )
