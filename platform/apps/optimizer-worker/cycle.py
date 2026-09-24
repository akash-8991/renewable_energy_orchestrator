"""One decision cycle (FRD §3.1, steps 1-5 — this service owns steps up to
"optimisation service computes feasible schedule"; risk/critic challenge,
policy disposition and command execution are later build phases).

1. Lock a trusted snapshot of current state (latest telemetry per asset).
2. Generate + persist forecasts for the horizon.
3. Build MILP inputs and solve the base (q50) plan.
4. Solve a risk-adjusted alternative plan (scenarios.risk_adjusted_series).
5. Independently validate both plans.
6. Persist a versioned Decision + Action rows (status=proposed,
   autonomy_mode=OBSERVE — Phase 5's policy/safety engine is what will
   later assign real disposition; until it exists, every cycle is
   correctly conservative: store and explain, never act).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from forecast import generate_and_persist_forecasts
from reo_common.db import SessionLocal, break_glass_cross_tenant
from reo_common.events import CloudEvent, EventBus, STREAM_DECISION_READY, new_correlation_id
from reo_common.models import (
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

HORIZON_HOURS = 24
STEP_HOURS = 1.0


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
                        solar_inputs.append(GenAssetInput(asset_id=asset.id, forecast_kw=series.q50))
                    elif asset.asset_type == "wind":
                        wind_inputs.append(GenAssetInput(asset_id=asset.id, forecast_kw=series.q50))
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

            decision = Decision(
                tenant_id=tenant_id,
                portfolio_id=portfolio.id,
                decision_cycle_id=correlation_id,
                version=1,
                trigger=trigger,
                horizon=f"{HORIZON_HOURS}h",
                autonomy_mode="OBSERVE",
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

            for s in base_result.steps[:1]:  # first step is the immediately actionable one; later steps stay in `plan` as the forward-looking schedule
                for asset_id, v in s.batteries.items():
                    if v["charge_kw"] > 1.0:
                        db.add(Action(
                            tenant_id=tenant_id, decision_id=decision.id, asset_id=asset_id, action_type="charge",
                            quantity=v["charge_kw"], unit="kW", start_time=now, end_time=now + timedelta(hours=STEP_HOURS),
                            envelope={"max_kw": v["charge_kw"]}, expiry=now + timedelta(minutes=15),
                            risk_level="low", reason="optimizer: charge from surplus/low price window",
                            expected_outcome={"soc_pct_after": v["soc_pct"]}, requires_approval=True,
                        ))
                    elif v["discharge_kw"] > 1.0:
                        db.add(Action(
                            tenant_id=tenant_id, decision_id=decision.id, asset_id=asset_id, action_type="discharge",
                            quantity=v["discharge_kw"], unit="kW", start_time=now, end_time=now + timedelta(hours=STEP_HOURS),
                            envelope={"max_kw": v["discharge_kw"]}, expiry=now + timedelta(minutes=15),
                            risk_level="low", reason="optimizer: discharge to meet demand/export at favourable price",
                            expected_outcome={"soc_pct_after": v["soc_pct"]}, requires_approval=True,
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
