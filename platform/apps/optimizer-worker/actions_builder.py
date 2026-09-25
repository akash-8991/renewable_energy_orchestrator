"""Translates one solved PlanStep into governed Action rows (extracted from
cycle.py so it's independently unit-testable — see test_actions_builder.py
in platform/tests). Battery charge/discharge, grid buy/sell, renewable
curtailment, and demand response all become Actions here; only
maintenance_advice has no path from plan to Action yet (see
docs/SIMPLIFICATIONS.md — no maintenance data model exists to act against).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from reo_common.autonomy import classify_risk
from reo_common.models import Action
from solver import PlanStep

MIN_ACTIONABLE_KW = 1.0


@dataclass
class AssetRef:
    """The minimal Asset fields this module needs — avoids importing the
    ORM model just to read two attributes, and makes the unit test able to
    construct one without a database."""
    id: str
    rated_capacity_kw: float


def build_actions_for_step(
    step: PlanStep,
    *,
    tenant_id: str,
    decision_id: str,
    binding_constraints: list[str],
    now: datetime,
    step_hours: float,
    approval_window_minutes: int,
    asset_by_id: dict[str, AssetRef],
    grid_asset: AssetRef,
    grid_max_import_kw: float,
    grid_max_export_kw: float,
) -> list[Action]:
    actions: list[Action] = []
    end_time = now + timedelta(hours=step_hours)
    expiry = now + timedelta(minutes=approval_window_minutes)

    for asset_id, v in step.batteries.items():
        asset = asset_by_id.get(asset_id)
        rated_kw = asset.rated_capacity_kw if asset else 0.0
        if v["charge_kw"] > MIN_ACTIONABLE_KW:
            risk = classify_risk("charge", v["charge_kw"], rated_kw, binding_constraints, asset_id)
            actions.append(Action(
                tenant_id=tenant_id, decision_id=decision_id, asset_id=asset_id, action_type="charge",
                quantity=v["charge_kw"], unit="kW", start_time=now, end_time=end_time,
                envelope={"max_kw": v["charge_kw"]}, expiry=expiry,
                risk_level=risk, reason="optimizer: charge from surplus/low price window",
                expected_outcome={"soc_pct_after": v["soc_pct"]}, requires_approval=True,
            ))
        elif v["discharge_kw"] > MIN_ACTIONABLE_KW:
            risk = classify_risk("discharge", v["discharge_kw"], rated_kw, binding_constraints, asset_id)
            actions.append(Action(
                tenant_id=tenant_id, decision_id=decision_id, asset_id=asset_id, action_type="discharge",
                quantity=v["discharge_kw"], unit="kW", start_time=now, end_time=end_time,
                envelope={"max_kw": v["discharge_kw"]}, expiry=expiry,
                risk_level=risk, reason="optimizer: discharge to meet demand/export at favourable price",
                expected_outcome={"soc_pct_after": v["soc_pct"]}, requires_approval=True,
            ))

    if step.import_kw > MIN_ACTIONABLE_KW:
        risk = classify_risk("buy", step.import_kw, grid_asset.rated_capacity_kw, binding_constraints, grid_asset.id)
        actions.append(Action(
            tenant_id=tenant_id, decision_id=decision_id, asset_id=grid_asset.id, action_type="buy",
            quantity=step.import_kw, unit="kW", start_time=now, end_time=end_time,
            envelope={"max_kw": grid_max_import_kw}, expiry=expiry,
            risk_level=risk, reason="optimizer: import from grid to cover shortfall/favourable price",
            expected_outcome={}, requires_approval=True,
        ))
    elif step.export_kw > MIN_ACTIONABLE_KW:
        risk = classify_risk("sell", step.export_kw, grid_asset.rated_capacity_kw, binding_constraints, grid_asset.id)
        actions.append(Action(
            tenant_id=tenant_id, decision_id=decision_id, asset_id=grid_asset.id, action_type="sell",
            quantity=step.export_kw, unit="kW", start_time=now, end_time=end_time,
            envelope={"max_kw": grid_max_export_kw}, expiry=expiry,
            risk_level=risk, reason="optimizer: export surplus to grid at favourable price",
            expected_outcome={}, requires_approval=True,
        ))

    for asset_id, curtailed_kw in step.curtailment.items():
        if curtailed_kw <= MIN_ACTIONABLE_KW:
            continue
        asset = asset_by_id.get(asset_id)
        rated_kw = asset.rated_capacity_kw if asset else 0.0
        risk = classify_risk("curtail", curtailed_kw, rated_kw, binding_constraints, asset_id)
        actions.append(Action(
            tenant_id=tenant_id, decision_id=decision_id, asset_id=asset_id, action_type="curtail",
            quantity=curtailed_kw, unit="kW", start_time=now, end_time=end_time,
            envelope={"max_kw": rated_kw}, expiry=expiry,
            risk_level=risk, reason="optimizer: curtail renewable output — binding grid/battery/demand limit leaves no other feasible option",
            expected_outcome={}, requires_approval=True,
        ))

    for asset_id, shed_kw in step.shed.items():
        if shed_kw <= MIN_ACTIONABLE_KW:
            continue
        asset = asset_by_id.get(asset_id)
        rated_kw = asset.rated_capacity_kw if asset else 0.0
        risk = classify_risk("demand_response", shed_kw, rated_kw, binding_constraints, asset_id)
        actions.append(Action(
            tenant_id=tenant_id, decision_id=decision_id, asset_id=asset_id, action_type="demand_response",
            quantity=shed_kw, unit="kW", start_time=now, end_time=end_time,
            envelope={"max_kw": rated_kw * 0.15}, expiry=expiry,
            risk_level=risk, reason="optimizer: shed non-critical load to stay within grid/reliability limits",
            expected_outcome={}, requires_approval=True,
        ))

    return actions
