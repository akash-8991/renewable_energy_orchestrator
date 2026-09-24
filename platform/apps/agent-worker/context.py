"""Assembles the evidence bundle each agent works from — one query pass
per decision cycle, sliced differently per agent's role. Every fact carries
an `evidence_id` so agent output can cite it (global system prompt rule 8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from reo_common.models import Action, Asset, Battery, Constraint, Decision, Forecast, ObjectivePolicy
from reo_common.twin import latest_readings_for_tenant
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass
class EvidenceBundle:
    decision: dict
    telemetry: list[dict]
    forecasts: list[dict]
    assets: list[dict]
    batteries: list[dict]
    constraints: list[dict]
    objective_policy: dict
    actions: list[dict]
    evidence_index: dict[str, dict] = field(default_factory=dict)


def _eid(prefix: str, n: int) -> str:
    return f"{prefix}-{n:04d}"


def build_evidence_bundle(db: Session, tenant_id: str, decision: Decision) -> EvidenceBundle:
    index: dict[str, dict] = {}

    readings = latest_readings_for_tenant(db, tenant_id)
    telemetry = []
    for i, r in enumerate(readings):
        eid = _eid("tel", i)
        row = {
            "evidence_id": eid, "asset_id": r.asset_id, "metric": r.metric, "value": r.value,
            "unit": r.unit, "quality": r.quality, "freshness": r.freshness, "confidence": r.confidence,
            "age_seconds": round(r.age_seconds, 1),
        }
        telemetry.append(row)
        index[eid] = row

    forecast_rows = db.execute(
        select(Forecast).where(Forecast.tenant_id == tenant_id, Forecast.issue_time == datetime.fromisoformat(decision.forecast_bundle_ref))
    ).scalars().all() if decision.forecast_bundle_ref else []
    forecasts = []
    for i, f in enumerate(forecast_rows):
        eid = _eid("fc", i)
        row = {
            "evidence_id": eid, "asset_id": f.asset_id, "variable": f.variable, "valid_time": f.valid_time.isoformat(),
            "quantile": f.quantile, "value": f.value, "unit": f.unit, "model_version": f.model_version, "is_fallback": f.is_fallback,
        }
        forecasts.append(row)
        index[eid] = row

    asset_rows = db.execute(select(Asset).where(Asset.tenant_id == tenant_id)).scalars().all()
    assets = []
    for i, a in enumerate(asset_rows):
        eid = _eid("asset", i)
        row = {
            "evidence_id": eid, "asset_id": a.id, "name": a.name, "asset_type": a.asset_type,
            "rated_capacity_kw": a.rated_capacity_kw, "availability": a.availability,
        }
        assets.append(row)
        index[eid] = row

    battery_rows = db.execute(select(Battery).where(Battery.tenant_id == tenant_id)).scalars().all()
    batteries = []
    for i, b in enumerate(battery_rows):
        eid = _eid("batt", i)
        row = {
            "evidence_id": eid, "asset_id": b.asset_id, "soh_pct": b.soh_pct,
            "energy_capacity_kwh": b.energy_capacity_kwh, "power_limit_kw": b.power_limit_kw,
            "soc_min_pct": b.soc_min_pct, "soc_max_pct": b.soc_max_pct,
            "warranty_cycles_remaining": b.warranty_cycles_remaining,
        }
        batteries.append(row)
        index[eid] = row

    constraint_rows = db.execute(select(Constraint).where(Constraint.tenant_id == tenant_id)).scalars().all()
    constraints = []
    for i, c in enumerate(constraint_rows):
        eid = _eid("con", i)
        row = {"evidence_id": eid, "scope": c.scope, "constraint_type": c.constraint_type, "expression": c.expression, "is_hard": c.is_hard}
        constraints.append(row)
        index[eid] = row

    policy = db.execute(
        select(ObjectivePolicy).where(ObjectivePolicy.tenant_id == tenant_id, ObjectivePolicy.is_active.is_(True))
    ).scalars().first()
    policy_dict = {
        "weights": policy.weights, "carbon_price_per_tonne": policy.carbon_price_per_tonne,
        "risk_aversion": policy.risk_aversion, "combination_method": policy.combination_method,
    } if policy else {}

    action_rows = db.execute(select(Action).where(Action.decision_id == decision.id)).scalars().all()
    actions = []
    for i, act in enumerate(action_rows):
        eid = _eid("act", i)
        row = {
            "evidence_id": eid, "asset_id": act.asset_id, "action_type": act.action_type,
            "quantity": act.quantity, "unit": act.unit, "risk_level": act.risk_level, "reason": act.reason,
        }
        actions.append(row)
        index[eid] = row

    decision_dict = {
        "decision_id": decision.id, "decision_cycle_id": decision.decision_cycle_id, "version": decision.version,
        "trigger": decision.trigger, "horizon": decision.horizon, "status": decision.status,
        "confidence": decision.confidence, "binding_constraints": decision.binding_constraints,
        "objective_value": decision.plan.get("objective_value") if decision.plan else None,
        "solver_status": decision.plan.get("status") if decision.plan else None,
        "alternatives": decision.alternatives,
        "n_plan_steps": len(decision.plan.get("steps", [])) if decision.plan else 0,
    }

    return EvidenceBundle(
        decision=decision_dict, telemetry=telemetry, forecasts=forecasts, assets=assets,
        batteries=batteries, constraints=constraints, objective_policy=policy_dict, actions=actions,
        evidence_index=index,
    )
