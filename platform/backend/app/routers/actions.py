"""Action Log ("Agent Action Tickets"): every governed `Action` the
platform's agents/optimizer ever proposed, presented as one ticket per
action with its resolution (approved/rejected/dispatched/pending) — not
just embedded inside a Decision's raw plan JSON where it was previously
only visible one decision at a time (`GET /decisions/{id}`), never as a
standalone, filterable, exportable list. `actions_builder.py` already
creates one governed `Action` row per action family (battery, grid,
curtailment, demand response) every cycle; this is the first place all of
them are queryable across every decision, in one page.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from models.canonical import Action, Approval, Asset, Decision, Signal
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionTicket(BaseModel):
    id: str
    action_type: str
    asset_id: str | None
    asset_name: str | None
    quantity: float
    unit: str
    risk_level: str
    requires_approval: bool
    reason: str | None
    start_time: str
    end_time: str
    decision_id: str
    decision_cycle_id: str
    decision_status: str
    ticket_status: str  # pending|approved|rejected|modified|held|expired|dispatched|acknowledged|failed
    signal_state: str | None
    approval_outcome: str | None
    created_at: str


def _ticket_status(signal: Signal | None, approval: Approval | None) -> str:
    if approval is not None and approval.outcome in ("rejected", "expired"):
        return approval.outcome
    if signal is not None:
        if signal.state in ("acknowledged", "reconciled"):
            return "dispatched"
        if signal.state in ("rejected", "timed_out", "cancelled", "rolled_back"):
            return "failed"
        if signal.state in ("queued", "sent", "approved"):
            return "dispatching"
    if approval is not None:
        return approval.outcome  # approved|modified|held
    return "pending"


@router.get("", response_model=list[ActionTicket])
def list_actions(
    status: str | None = Query(None, description="filter by derived ticket_status"),
    action_type: str | None = Query(None),
    risk_level: str | None = Query(None),
    asset_id: str | None = Query(None, description="filter to actions taken against one asset"),
    since: datetime | None = Query(None),
    limit: int = Query(200, le=2000),
    ctx: AuthContext = Depends(require_permission("read:decisions")),
    db: Session = Depends(db_session),
) -> list[ActionTicket]:
    stmt = select(Action).order_by(Action.start_time.desc()).limit(limit)
    if action_type:
        stmt = stmt.where(Action.action_type == action_type)
    if risk_level:
        stmt = stmt.where(Action.risk_level == risk_level)
    if asset_id:
        stmt = stmt.where(Action.asset_id == asset_id)
    if since:
        stmt = stmt.where(Action.start_time >= since)
    actions = db.execute(stmt).scalars().all()
    if not actions:
        return []

    action_ids = [a.id for a in actions]
    decision_ids = list({a.decision_id for a in actions})
    asset_ids = list({a.asset_id for a in actions if a.asset_id})

    decisions = {d.id: d for d in db.execute(select(Decision).where(Decision.id.in_(decision_ids))).scalars().all()}
    assets = {a.id: a for a in db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars().all()} if asset_ids else {}
    signals_by_action: dict[str, Signal] = {}
    for s in db.execute(select(Signal).where(Signal.action_id.in_(action_ids)).order_by(Signal.created_at.desc())).scalars().all():
        signals_by_action.setdefault(s.action_id, s)  # first (=latest, due to ORDER BY) wins per action
    approvals_by_action: dict[str, Approval] = {}
    for ap in db.execute(select(Approval).where(Approval.action_id.in_(action_ids)).order_by(Approval.created_at.desc())).scalars().all():
        approvals_by_action.setdefault(ap.action_id, ap)

    out = []
    for a in actions:
        decision = decisions.get(a.decision_id)
        asset = assets.get(a.asset_id) if a.asset_id else None
        signal = signals_by_action.get(a.id)
        approval = approvals_by_action.get(a.id)
        ticket_status = _ticket_status(signal, approval)
        if status and ticket_status != status:
            continue
        out.append(ActionTicket(
            id=a.id, action_type=a.action_type, asset_id=a.asset_id, asset_name=asset.name if asset else None,
            quantity=a.quantity, unit=a.unit, risk_level=a.risk_level, requires_approval=a.requires_approval,
            reason=a.reason, start_time=a.start_time.isoformat(), end_time=a.end_time.isoformat(),
            decision_id=a.decision_id, decision_cycle_id=decision.decision_cycle_id if decision else "",
            decision_status=decision.status if decision else "",
            ticket_status=ticket_status, signal_state=signal.state if signal else None,
            approval_outcome=approval.outcome if approval else None, created_at=a.start_time.isoformat(),
        ))
    return out
