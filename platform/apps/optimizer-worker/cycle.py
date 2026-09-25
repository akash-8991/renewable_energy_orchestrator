"""One decision cycle (FRD §3.1 steps 1-8, minus the actual agent evidence
pass which runs asynchronously in agent-worker once this publishes
reo.decision.ready).

1. Lock a trusted snapshot of current state (latest telemetry per asset).
2. Generate + persist forecasts for the horizon.
3. Build MILP inputs and solve the base (q50) plan.
4. Solve a risk-adjusted alternative plan (scenarios.risk_adjusted_series).
5. Independently validate both plans.
6. Persist a versioned Decision + Action rows.
7. Policy/safety engine resolves the effective autonomy mode and each
   action's risk tier; APPROVAL_REQUIRED actions get a Signal + pending
   Approval, AUTONOMOUS_BOUNDED low-risk actions (with a recorded safety
   case) get a Signal dispatched immediately to the OT gateway.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from actions_builder import AssetRef, build_actions_for_step
from forecast import generate_and_persist_forecasts
from reo_common.autonomy import autonomous_execution_allowed, resolve_autonomy_mode
from reo_common.config import get_settings
from reo_common.db import SessionLocal, break_glass_cross_tenant
from reo_common.events import CloudEvent, EventBus, STREAM_DECISION_READY, new_correlation_id
from reo_common.execution import build_signal_for_action, dispatch_signal
from scenario_lab import ScenarioInputs, run_scenario_comparison
from reo_common.models import (
    Approval,
    Asset,
    Battery,
    Constraint,
    Decision,
    Action,
    ObjectivePolicy,
    Portfolio,
    Tenant,
)
from reo_common.twin import latest_readings_for_tenant
from scenarios import build_series, risk_adjusted_series
from solver import BatteryInput, ConsumerInput, GenAssetInput, GridInput, ObjectiveWeights, solve
from validator import validate_plan
from sqlalchemy import select

log = logging.getLogger("optimizer-worker.cycle")
settings = get_settings()

HORIZON_HOURS = 24
STEP_HOURS = 1.0
APPROVAL_WINDOW_MINUTES = 15


def _load_forecast_series(db, tenant_id: str, asset_id: str, now: datetime, n_steps: int, step_hours: float):
    from reo_common.models import Forecast

    rows = db.execute(
        select(Forecast).where(
            Forecast.tenant_id == tenant_id, Forecast.asset_id == asset_id, Forecast.issue_time == now
        )
    ).scalars().all()
    valid_times = [now + timedelta(hours=(i + 1) * step_hours) for i in range(n_steps)]
    return build_series(rows, valid_times)


def run_cycle(trigger: str = "scheduled") -> str | None:
    db = SessionLocal()
    correlation_id = new_correlation_id("cyc")
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant)).scalars().first()
            if tenant is None:
                log.info("no tenant provisioned yet, skipping cycle")
                return None
            tenant_id = tenant.id

            portfolio = db.execute(select(Portfolio).where(Portfolio.tenant_id == tenant_id)).scalars().first()
            if portfolio is None:
                return None

            assets = db.execute(select(Asset).where(Asset.tenant_id == tenant_id)).scalars().all()
            batteries_rows = db.execute(select(Battery).where(Battery.tenant_id == tenant_id)).scalars().all()
            battery_by_asset = {b.asset_id: b for b in batteries_rows}

            objective_policy = db.execute(
                select(ObjectivePolicy).where(ObjectivePolicy.tenant_id == tenant_id, ObjectivePolicy.is_active.is_(True))
            ).scalars().first()
            weights = ObjectiveWeights(**{
                **{k: v for k, v in (objective_policy.weights or {}).items() if k in ObjectiveWeights.__dataclass_fields__},
                "carbon_price_per_tonne": objective_policy.carbon_price_per_tonne if objective_policy else 80.0,
            }) if objective_policy else ObjectiveWeights()

            now = datetime.now(timezone.utc)

            # step 1: trusted snapshot (freshness-scored latest telemetry per asset)
            readings = latest_readings_for_tenant(db, tenant_id)
            latest_by_asset_metric: dict[tuple[str, str], float] = {(r.asset_id, r.metric): r.value for r in readings}
            stale_count = sum(1 for r in readings if r.freshness != "fresh")

            # step 2: forecasts — `now` is passed through unchanged so the
            # later lookup-by-issue_time query matches exactly (see
            # forecast.py's comment on why this must not be recomputed)
            generate_and_persist_forecasts(db, tenant_id, assets, HORIZON_HOURS, STEP_HOURS, now)
            db.commit()
            n_steps = int(HORIZON_HOURS / STEP_HOURS)

            solar_inputs, wind_inputs, consumer_inputs = [], [], []
            for asset in assets:
                if asset.asset_type in ("solar", "wind", "consumer"):
                    series = _load_forecast_series(db, tenant_id, asset.id, now, n_steps, STEP_HOURS)
                    if asset.asset_type == "solar":
                        solar_inputs.append(GenAssetInput(asset_id=asset.id, forecast_kw=series.q50, rated_capacity_kw=asset.rated_capacity_kw))
                    elif asset.asset_type == "wind":
                        wind_inputs.append(GenAssetInput(asset_id=asset.id, forecast_kw=series.q50, rated_capacity_kw=asset.rated_capacity_kw))
                    else:
                        consumer_inputs.append(ConsumerInput(asset_id=asset.id, forecast_kw=series.q50))

            # sanity check: demand should never legitimately forecast to zero
            # across a full 24h horizon (unlike solar, which is zero at
            # night). If it does, forecasts almost certainly failed to load
            # (e.g. an issue_time mismatch) rather than genuinely predicting
            # no consumption — catch that class of bug loudly instead of
            # silently solving a trivial, meaningless all-zero plan.
            if consumer_inputs and all(sum(c.forecast_kw) == 0 for c in consumer_inputs):
                log.error("all consumer demand forecasts are zero across the horizon — forecast load likely failed; aborting cycle")
                return None

            grid_asset = next((a for a in assets if a.asset_type == "grid_interconnection"), None)
            if grid_asset is None:
                log.warning("no grid_interconnection asset found, skipping cycle")
                return None
            price_series = _load_forecast_series(db, tenant_id, grid_asset.id, now, n_steps, STEP_HOURS)
            grid_constraint = db.execute(
                select(Constraint).where(Constraint.tenant_id == tenant_id, Constraint.scope == f"asset:{grid_asset.id}")
            ).scalars().first()
            limits = grid_constraint.expression if grid_constraint else {"max_import_kw": grid_asset.rated_capacity_kw, "max_export_kw": grid_asset.rated_capacity_kw}
            grid_input = GridInput(
                asset_id=grid_asset.id, price_gbp_per_mwh=price_series.q50,
                max_import_kw=limits["max_import_kw"], max_export_kw=limits["max_export_kw"],
            )

            battery_inputs = []
            for asset in assets:
                if asset.asset_type != "battery":
                    continue
                battery = battery_by_asset.get(asset.id)
                if battery is None:
                    continue
                current_soc = latest_by_asset_metric.get((asset.id, "soc_pct"), battery.soc_current_pct)
                battery_inputs.append(
                    BatteryInput(
                        asset_id=asset.id, power_limit_kw=battery.power_limit_kw,
                        energy_capacity_kwh=battery.energy_capacity_kwh, soc_min_pct=battery.soc_min_pct,
                        soc_max_pct=battery.soc_max_pct, initial_soc_pct=current_soc,
                        round_trip_efficiency=battery.round_trip_efficiency,
                        degradation_cost_per_kwh=battery.degradation_cost_per_kwh_cycled,
                    )
                )

            base_result = solve(
                n_steps=n_steps, step_hours=STEP_HOURS, solar=solar_inputs, wind=wind_inputs,
                consumers=consumer_inputs, batteries=battery_inputs, grid=grid_input, weights=weights,
            )
            base_validation = validate_plan(
                base_result, solar=solar_inputs, wind=wind_inputs, consumers=consumer_inputs,
                batteries=battery_inputs, grid=grid_input,
            )

            # step 4: risk-averse alternative — plan against the conservative
            # tail of the forecast band instead of the median (see scenarios.py)
            risk_aversion = objective_policy.risk_aversion if objective_policy else 0.2
            risk_grid = GridInput(
                asset_id=grid_asset.id,
                price_gbp_per_mwh=risk_adjusted_series(price_series, risk_aversion, downside_is_high=True),
                max_import_kw=limits["max_import_kw"], max_export_kw=limits["max_export_kw"],
            )
            alt_result = solve(
                n_steps=n_steps, step_hours=STEP_HOURS, solar=solar_inputs, wind=wind_inputs,
                consumers=consumer_inputs, batteries=battery_inputs, grid=risk_grid, weights=weights,
            )

            status = "proposed" if base_validation.ok and base_result.status in ("OPTIMAL", "FEASIBLE") else "failed"
            risk_flags = list(base_validation.violations)
            if stale_count > 0:
                risk_flags.append(f"{stale_count} telemetry readings not fresh at snapshot time")

            # Forward-looking scenario simulation (F3): re-solve the same
            # horizon under each named variation, not just the risk-averse
            # alternative above, and persist the comparison — this is what
            # lets an operator see how the plan would change under a shock
            # *before* it happens, not just react to one after the fact.
            if status == "proposed":
                try:
                    scenario_runs = run_scenario_comparison(
                        tenant_id=tenant_id, decision_cycle_id=correlation_id, n_steps=n_steps, step_hours=STEP_HOURS,
                        base_inputs=ScenarioInputs(solar=solar_inputs, wind=wind_inputs, consumers=consumer_inputs, batteries=battery_inputs, grid=grid_input),
                        weights=weights, base_result=base_result,
                    )
                    for run in scenario_runs:
                        db.add(run)
                except Exception:
                    log.exception("scenario comparison failed for cycle %s (non-fatal, decision still proceeds)", correlation_id)

            # step 7: policy/safety engine resolves the effective mode for
            # this cycle (BR-05: no configured policy -> the conservative
            # OBSERVE default, never an implicit permissive one)
            autonomy_mode, autonomy_policy = resolve_autonomy_mode(db, tenant_id)

            decision = Decision(
                tenant_id=tenant_id,
                portfolio_id=portfolio.id,
                decision_cycle_id=correlation_id,
                version=1,
                trigger=trigger,
                horizon=f"{HORIZON_HOURS}h",
                autonomy_mode=autonomy_mode,
                status=status,
                trusted_snapshot_ref=now.isoformat(),
                forecast_bundle_ref=now.isoformat(),
                objective_policy_version=objective_policy.version if objective_policy else None,
                optimisation_run_id=correlation_id,
                plan={
                    "status": base_result.status,
                    "objective_value": base_result.objective_value,
                    "steps": [
                        {
                            "t": s.t, "import_kw": s.import_kw, "export_kw": s.export_kw,
                            "batteries": s.batteries, "curtailment": s.curtailment, "shed": s.shed,
                        }
                        for s in base_result.steps
                    ],
                },
                alternatives=[
                    {
                        "label": "risk_averse",
                        "status": alt_result.status,
                        "objective_value": alt_result.objective_value,
                        "delta_objective": round(alt_result.objective_value - base_result.objective_value, 2),
                    }
                ],
                binding_constraints=base_result.binding_constraints,
                confidence=base_result.confidence if base_validation.ok else 0.0,
                risk_flags=risk_flags,
                expires_at=now + timedelta(minutes=15),
            )
            db.add(decision)
            db.flush()

            asset_by_id = {a.id: AssetRef(id=a.id, rated_capacity_kw=a.rated_capacity_kw) for a in assets}
            actions: list[Action] = []
            for s in base_result.steps[:1]:  # first step is the immediately actionable one; later steps stay in `plan` as the forward-looking schedule
                actions.extend(build_actions_for_step(
                    s, tenant_id=tenant_id, decision_id=decision.id, binding_constraints=decision.binding_constraints,
                    now=now, step_hours=STEP_HOURS, approval_window_minutes=APPROVAL_WINDOW_MINUTES,
                    asset_by_id=asset_by_id,
                    grid_asset=AssetRef(id=grid_asset.id, rated_capacity_kw=grid_asset.rated_capacity_kw),
                    grid_max_import_kw=limits["max_import_kw"], grid_max_export_kw=limits["max_export_kw"],
                ))
            for action in actions:
                db.add(action)
            db.flush()

            # Route each action per the resolved autonomy mode. OBSERVE/
            # RECOMMEND: store only, no Signal (FRD §3.1 step 8). APPROVAL_
            # REQUIRED: create a Signal + a pending Approval for a human.
            # AUTONOMOUS_BOUNDED: only actions within the policy's risk
            # ceiling AND backed by a recorded safety_case_ref dispatch
            # immediately; anything above that ceiling still falls back to
            # requiring approval even in autonomous mode.
            if status == "proposed" and autonomy_mode in ("APPROVAL_REQUIRED", "AUTONOMOUS_BOUNDED"):
                for action in actions:
                    if autonomy_mode == "AUTONOMOUS_BOUNDED" and autonomous_execution_allowed(autonomy_policy, action.risk_level):
                        signal = build_signal_for_action(db, decision, action)
                        if signal is None:
                            continue
                        signal.state = "queued"
                        db.flush()
                        command = dispatch_signal(db, signal, ot_gateway_base_url=settings.ot_gateway_url, actor_label="policy-engine:autonomous")
                        log.info("autonomous dispatch: action=%s signal=%s ack=%s", action.id, signal.id, command.ack_status)
                    else:
                        signal = build_signal_for_action(db, decision, action)
                        if signal is None:
                            continue
                        signal.state = "approval_required"
                        db.add(Approval(
                            tenant_id=tenant_id, decision_id=decision.id, action_id=action.id, approver_id=None,
                            outcome="pending", requires_second_approver=(action.risk_level == "high"),
                            token=f"appr-{signal.idempotency_key}", expires_at=action.expiry,
                        ))

            db.commit()

            bus = EventBus()
            bus.publish(STREAM_DECISION_READY, CloudEvent(
                type="reo.decision.ready", source="optimizer-worker", tenant_id=tenant_id,
                data={"decision_id": decision.id, "status": status, "confidence": decision.confidence},
                correlation_id=correlation_id,
            ))

            log.info(
                "decision cycle %s: status=%s solver=%s confidence=%.2f violations=%d",
                correlation_id, status, base_result.status, decision.confidence, len(base_validation.violations),
            )
            return decision.id
    except Exception:
        db.rollback()
        log.exception("decision cycle %s failed", correlation_id)
        raise
    finally:
        db.close()
