"""Approval Inbox, autonomy policy administration, and e-stop (FR-APP-001/
002, FR-GV-001/003). The actual dispatch-on-approval call reaches the OT
gateway through the same reo_common.execution.dispatch_signal() path the
autonomous branch of the decision cycle uses — one code path for "how a
Signal gets dispatched", regardless of what authorised it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from reo_common.audit import append_audit_event
from reo_common.config import get_settings
from reo_common.execution import dispatch_signal
from reo_common.models import Approval, AutonomyPolicy, Decision, ObjectivePolicy, Signal
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/governance", tags=["governance"])
settings = get_settings()


class ApprovalSummary(BaseModel):
    id: str
    decision_id: str
    action_id: str | None
    outcome: str
    requires_second_approver: bool
    expires_at: str
    created_at: str


@router.get("/approvals", response_model=list[ApprovalSummary])
def list_approvals(
    outcome: str = "pending",
    ctx: AuthContext = Depends(require_permission("read:decisions")),
    db: Session = Depends(db_session),
) -> list[ApprovalSummary]:
    rows = db.execute(select(Approval).where(Approval.outcome == outcome).order_by(Approval.created_at.desc())).scalars().all()
    return [
        ApprovalSummary(
            id=a.id, decision_id=a.decision_id, action_id=a.action_id, outcome=a.outcome,
            requires_second_approver=a.requires_second_approver,
            expires_at=a.expires_at.isoformat(), created_at=a.created_at.isoformat(),
        )
        for a in rows
    ]


class ApprovalDecisionRequest(BaseModel):
    outcome: Literal["approved", "rejected", "held"]
    reason: str | None = None


class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    outcome: str
    signal_state: str | None = None
    dispatch_acknowledged: bool | None = None


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalDecisionResponse)
def decide_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    ctx: AuthContext = Depends(require_permission("approve:assigned")),
    db: Session = Depends(db_session),
) -> ApprovalDecisionResponse:
    approval = db.execute(select(Approval).where(Approval.id == approval_id)).scalar_one_or_none()
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    if approval.outcome != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"approval already decided: {approval.outcome}")
    if datetime.now(timezone.utc) > approval.expires_at:
        approval.outcome = "expired"
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "approval window has expired — the decision must be re-planned")

    if approval.requires_second_approver and approval.approver_id and approval.approver_id != ctx.user_id and not ctx.has_permission("approve:four_eyes"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this high-risk approval requires a second, different approver")
    if approval.approver_id == ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "an approver cannot approve their own prior decision on this item (no self-approval)")

    approval.outcome = body.outcome
    approval.approver_id = ctx.user_id
    approval.reason = body.reason
    db.flush()

    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="approval.decided", payload={"approval_id": approval.id, "outcome": body.outcome, "reason": body.reason},
    )

    signal_state = None
    dispatch_acknowledged = None
    if body.outcome == "approved" and approval.action_id:
        decision = db.execute(select(Decision).where(Decision.id == approval.decision_id)).scalar_one_or_none()
        idempotency_key = f"{approval.decision_id}:{approval.action_id}"
        signal = db.execute(select(Signal).where(Signal.idempotency_key == idempotency_key)).scalar_one_or_none()
        if signal is not None and decision is not None:
            signal.approval_ref = approval.token
            signal.state = "approved"
            db.flush()
            signal.state = "queued"
            db.flush()
            command = dispatch_signal(db, signal, ot_gateway_base_url=settings.ot_gateway_url, actor_label=f"approval:{ctx.email}")
            signal_state = signal.state
            dispatch_acknowledged = command.ack_status == "acknowledged"

    db.commit()
    return ApprovalDecisionResponse(approval_id=approval.id, outcome=approval.outcome, signal_state=signal_state, dispatch_acknowledged=dispatch_acknowledged)


class AutonomyPolicyRequest(BaseModel):
    scope: str = "portfolio"
    mode: Literal["OBSERVE", "RECOMMEND", "APPROVAL_REQUIRED", "AUTONOMOUS_BOUNDED"]
    max_action_risk: Literal["low", "medium", "high"] = "low"
    safety_case_ref: str | None = None


class AutonomyPolicyResponse(BaseModel):
    id: str
    scope: str
    mode: str
    max_action_risk: str
    safety_case_ref: str | None
    effective_from: str


@router.put("/autonomy-policy", response_model=AutonomyPolicyResponse)
def set_autonomy_policy(
    body: AutonomyPolicyRequest,
    ctx: AuthContext = Depends(require_permission("manage:policies")),
    db: Session = Depends(db_session),
) -> AutonomyPolicyResponse:
    if body.mode == "AUTONOMOUS_BOUNDED" and not body.safety_case_ref:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "AUTONOMOUS_BOUNDED requires a safety_case_ref (doc 05 §7: formal hazard analysis and a "
            "client-specific safety case are required before autonomous production) — record its "
            "reference here, this endpoint will not fabricate one.",
        )
    now = datetime.now(timezone.utc)
    # supersede any existing policy at this scope rather than stacking ambiguous overlapping rows
    existing = db.execute(
        select(AutonomyPolicy).where(AutonomyPolicy.tenant_id == ctx.tenant_id, AutonomyPolicy.scope == body.scope, AutonomyPolicy.effective_to.is_(None))
    ).scalars().all()
    for e in existing:
        e.effective_to = now

    policy = AutonomyPolicy(
        tenant_id=ctx.tenant_id, scope=body.scope, mode=body.mode, max_action_risk=body.max_action_risk,
        safety_case_ref=body.safety_case_ref, set_by=ctx.user_id, effective_from=now,
    )
    db.add(policy)
    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="autonomy_policy.changed", payload={"scope": body.scope, "mode": body.mode, "safety_case_ref": body.safety_case_ref},
    )
    db.commit()
    return AutonomyPolicyResponse(
        id=policy.id, scope=policy.scope, mode=policy.mode, max_action_risk=policy.max_action_risk,
        safety_case_ref=policy.safety_case_ref, effective_from=policy.effective_from.isoformat(),
    )


@router.get("/autonomy-policy", response_model=list[AutonomyPolicyResponse])
def get_autonomy_policies(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)
) -> list[AutonomyPolicyResponse]:
    rows = db.execute(
        select(AutonomyPolicy).where(AutonomyPolicy.tenant_id == ctx.tenant_id, AutonomyPolicy.effective_to.is_(None)).order_by(AutonomyPolicy.effective_from.desc())
    ).scalars().all()
    return [
        AutonomyPolicyResponse(
            id=p.id, scope=p.scope, mode=p.mode, max_action_risk=p.max_action_risk,
            safety_case_ref=p.safety_case_ref, effective_from=p.effective_from.isoformat(),
        )
        for p in rows
    ]


class EstopRequest(BaseModel):
    active: bool
    reason: str


@router.post("/e-stop")
def trigger_estop(
    body: EstopRequest,
    ctx: AuthContext = Depends(require_permission("e_stop:trigger")),
    db: Session = Depends(db_session),
) -> dict:
    import redis as redis_lib

    r = redis_lib.from_url(settings.redis_url, decode_responses=True)
    key = f"reo:estop:{ctx.tenant_id}"
    if body.active:
        r.set(key, "1")
    else:
        r.delete(key)
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="e_stop.triggered", payload={"active": body.active, "reason": body.reason},
    )
    db.commit()
    return {"tenant_id": ctx.tenant_id, "active": body.active}


# ---------------------------------------------------------------------------
# Objective policy (PRD "optimality criteria" — hackathon problem 4's F2:
# "determine optimal cluster of options after determining optimality
# criteria given a specific situation and adjusting for uncertainty" reads
# on the ability to actually SET those criteria, not just have the
# optimizer apply a fixed set baked in at seed time).
# ---------------------------------------------------------------------------


class ObjectivePolicyWeights(BaseModel):
    cost: float = Field(0.35, ge=0, le=1)
    degradation: float = Field(0.1, ge=0, le=1)
    carbon: float = Field(0.15, ge=0, le=1)
    curtailment: float = Field(0.15, ge=0, le=1)
    reliability: float = Field(0.1, ge=0, le=1)


class ObjectivePolicyRequest(BaseModel):
    weights: ObjectivePolicyWeights
    carbon_price_per_tonne: float = Field(80.0, ge=0)
    risk_aversion: float = Field(0.2, ge=0, le=1, description="0=plan to the median forecast, 1=plan to the full q90 tail (fully risk-averse)")
    combination_method: str = "lexicographic_safety_then_weighted_sum"


class ObjectivePolicyResponse(BaseModel):
    id: str
    version: int
    weights: dict
    carbon_price_per_tonne: float
    risk_aversion: float
    combination_method: str
    approved_by: str | None
    created_at: str


@router.get("/objective-policy", response_model=ObjectivePolicyResponse)
def get_objective_policy(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)
) -> ObjectivePolicyResponse:
    policy = db.execute(
        select(ObjectivePolicy).where(ObjectivePolicy.tenant_id == ctx.tenant_id, ObjectivePolicy.is_active.is_(True))
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active objective policy for this tenant")
    return ObjectivePolicyResponse(
        id=policy.id, version=policy.version, weights=policy.weights, carbon_price_per_tonne=policy.carbon_price_per_tonne,
        risk_aversion=policy.risk_aversion, combination_method=policy.combination_method, approved_by=policy.approved_by,
        created_at=policy.created_at.isoformat(),
    )


@router.put("/objective-policy", response_model=ObjectivePolicyResponse)
def set_objective_policy(
    body: ObjectivePolicyRequest,
    ctx: AuthContext = Depends(require_permission("manage:objective_policy")),
    db: Session = Depends(db_session),
) -> ObjectivePolicyResponse:
    """Creates a new *version* rather than mutating in place — the previous
    version stays in the table (is_active=False) so every past Decision's
    `objective_policy_version` reference keeps meaning what it meant when
    that decision was made (BR-06: retain objective weights alongside the
    decision they governed)."""
    current = db.execute(
        select(ObjectivePolicy).where(ObjectivePolicy.tenant_id == ctx.tenant_id, ObjectivePolicy.is_active.is_(True))
    ).scalar_one_or_none()
    if current is not None:
        current.is_active = False

    policy = ObjectivePolicy(
        tenant_id=ctx.tenant_id,
        version=(current.version + 1) if current else 1,
        weights=body.weights.model_dump(),
        carbon_price_per_tonne=body.carbon_price_per_tonne,
        risk_aversion=body.risk_aversion,
        combination_method=body.combination_method,
        approved_by=ctx.email,
        is_active=True,
    )
    db.add(policy)
    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="objective_policy.changed",
        payload={"version": policy.version, "weights": policy.weights, "risk_aversion": policy.risk_aversion, "carbon_price_per_tonne": policy.carbon_price_per_tonne},
    )
    db.commit()
    return ObjectivePolicyResponse(
        id=policy.id, version=policy.version, weights=policy.weights, carbon_price_per_tonne=policy.carbon_price_per_tonne,
        risk_aversion=policy.risk_aversion, combination_method=policy.combination_method, approved_by=policy.approved_by,
        created_at=policy.created_at.isoformat(),
    )
