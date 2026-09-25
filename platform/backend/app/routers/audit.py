"""Audit Evidence Service (FR-AU-001, TR-AUD-01): read + cryptographic
verification of the tamper-evident audit chain, and an evidence pack export
for a specific decision (retrievable by decision ID per BRD §8's "Audit"
success criterion: "Complete evidence chain retrievable by decision ID").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from output.audit import verify_chain
from models.canonical import Action, Approval, AuditEvent, Decision, Signal
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    id: str
    event_type: str
    actor_label: str
    payload: dict
    hash: str
    prev_hash: str
    created_at: str


class AuditChainResponse(BaseModel):
    events: list[AuditEventOut]
    chain_valid: bool
    broken_at_event_id: str | None


@router.get("/events", response_model=AuditChainResponse)
def list_audit_events(
    event_type: str | None = Query(None),
    limit: int = Query(200, le=2000),
    ctx: AuthContext = Depends(require_permission("read:audit")),
    db: Session = Depends(db_session),
) -> AuditChainResponse:
    # Verification MUST run over the tenant's full, unfiltered chain from
    # genesis — verify_chain expects a contiguous prev_hash->hash sequence,
    # and either an event_type filter or a trailing-N slice removes entries
    # from the middle/start of that sequence, which makes a perfectly valid
    # chain look "broken" for a reason that has nothing to do with tampering.
    # `events` returned to the caller is a separate, filtered/limited view.
    full_chain = db.execute(
        select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant_id).order_by(AuditEvent.created_at.asc())
    ).scalars().all()
    ok, broken_id = verify_chain(full_chain)

    display_rows = full_chain
    if event_type:
        display_rows = [e for e in display_rows if e.event_type == event_type]
    display_rows = display_rows[-limit:]

    return AuditChainResponse(
        events=[
            AuditEventOut(id=e.id, event_type=e.event_type, actor_label=e.actor_label, payload=e.payload,
                           hash=e.hash, prev_hash=e.prev_hash, created_at=e.created_at.isoformat())
            for e in display_rows
        ],
        chain_valid=ok, broken_at_event_id=broken_id,
    )


class EvidencePack(BaseModel):
    decision: dict
    actions: list[dict]
    approvals: list[dict]
    signals: list[dict]
    related_audit_events: list[AuditEventOut]
    chain_valid: bool


@router.get("/evidence/{decision_id}", response_model=EvidencePack)
def get_evidence_pack(
    decision_id: str, ctx: AuthContext = Depends(require_permission("read:audit")), db: Session = Depends(db_session)
) -> EvidencePack:
    decision = db.execute(select(Decision).where(Decision.id == decision_id)).scalar_one_or_none()
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision not found")

    actions = db.execute(select(Action).where(Action.decision_id == decision_id)).scalars().all()
    approvals = db.execute(select(Approval).where(Approval.decision_id == decision_id)).scalars().all()
    signals = db.execute(select(Signal).where(Signal.action_id.in_([a.id for a in actions]))).scalars().all() if actions else []

    all_tenant_events = db.execute(
        select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant_id).order_by(AuditEvent.created_at.asc())
    ).scalars().all()
    related = [e for e in all_tenant_events if decision_id in str(e.payload)]
    ok, _ = verify_chain(all_tenant_events)

    return EvidencePack(
        decision={
            "id": decision.id, "decision_cycle_id": decision.decision_cycle_id, "status": decision.status,
            "autonomy_mode": decision.autonomy_mode, "confidence": decision.confidence, "plan": decision.plan,
            "alternatives": decision.alternatives, "reasoning": decision.reasoning, "risk_flags": decision.risk_flags,
            "binding_constraints": decision.binding_constraints, "created_at": decision.created_at.isoformat(),
        },
        actions=[{"id": a.id, "asset_id": a.asset_id, "action_type": a.action_type, "quantity": a.quantity, "risk_level": a.risk_level} for a in actions],
        approvals=[{"id": a.id, "outcome": a.outcome, "approver_id": a.approver_id, "reason": a.reason} for a in approvals],
        signals=[{"id": s.id, "state": s.state, "command_type": s.command_type, "setpoint_value": s.setpoint_value} for s in signals],
        related_audit_events=[
            AuditEventOut(id=e.id, event_type=e.event_type, actor_label=e.actor_label, payload=e.payload,
                           hash=e.hash, prev_hash=e.prev_hash, created_at=e.created_at.isoformat())
            for e in related
        ],
        chain_valid=ok,
    )
