"""Solver + independent validator, exercised directly (no DB) with a small
synthetic portfolio. Regression-guards the forecast issue_time bug: a
solar+wind+battery+consumer+grid system across 6 hourly steps must produce
a genuinely balanced plan, not a trivial all-zero one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "optimizer-worker"))

from solver import BatteryInput, ConsumerInput, GenAssetInput, GridInput, ObjectiveWeights, solve  # noqa: E402
from validator import validate_plan  # noqa: E402

N_STEPS = 6
STEP_HOURS = 1.0


def _base_inputs():
    # solar ramps up then down; wind flat 10 MW; demand flat 15 MW
    solar = [GenAssetInput(asset_id="solar-1", forecast_kw=[0, 5000, 15000, 15000, 5000, 0])]
    wind = [GenAssetInput(asset_id="wind-1", forecast_kw=[10000] * N_STEPS)]
    consumers = [ConsumerInput(asset_id="consumer-1", forecast_kw=[15000] * N_STEPS, max_shed_frac=0.1)]
    batteries = [
        BatteryInput(
            asset_id="battery-1", power_limit_kw=5000, energy_capacity_kwh=10000,
            soc_min_pct=10, soc_max_pct=95, initial_soc_pct=50, round_trip_efficiency=0.9,
            degradation_cost_per_kwh=0.02,
        )
    ]
    grid = GridInput(asset_id="grid-1", price_gbp_per_mwh=[60, 60, 40, 40, 80, 80], max_import_kw=20000, max_export_kw=20000)
    return solar, wind, consumers, batteries, grid


def test_solver_produces_optimal_nontrivial_plan():
    solar, wind, consumers, batteries, grid = _base_inputs()
    result = solve(n_steps=N_STEPS, step_hours=STEP_HOURS, solar=solar, wind=wind, consumers=consumers, batteries=batteries, grid=grid, weights=ObjectiveWeights())

    assert result.status == "OPTIMAL"
    # not a trivial all-zero plan: at t=0 (solar=0, wind=10MW, demand=15MW)
    # the 5MW gap must come from somewhere (import or battery discharge)
    t0 = result.steps[0]
    covered = t0.import_kw + sum(v["discharge_kw"] - v["charge_kw"] for v in t0.batteries.values())
    assert covered > 100, "expected import or battery discharge to cover the solar/wind shortfall at t=0"


def test_independent_validator_accepts_solver_output():
    solar, wind, consumers, batteries, grid = _base_inputs()
    result = solve(n_steps=N_STEPS, step_hours=STEP_HOURS, solar=solar, wind=wind, consumers=consumers, batteries=batteries, grid=grid, weights=ObjectiveWeights())
    validation = validate_plan(result, solar=solar, wind=wind, consumers=consumers, batteries=batteries, grid=grid)
    assert validation.ok, validation.violations


def test_battery_outage_forces_zero_power():
    solar, wind, consumers, batteries, grid = _base_inputs()
    batteries[0].available = False
    result = solve(n_steps=N_STEPS, step_hours=STEP_HOURS, solar=solar, wind=wind, consumers=consumers, batteries=batteries, grid=grid, weights=ObjectiveWeights())
    assert result.status == "OPTIMAL"
    for step in result.steps:
        v = step.batteries["battery-1"]
        assert v["charge_kw"] == 0
        assert v["discharge_kw"] == 0


def test_validator_flags_manufactured_balance_violation():
    """Regression test for the forecast-issue_time bug's failure mode: a
    plan that claims balance without actually summing correctly must be
    caught. We feed the validator a hand-built SolveResult with a step
    whose numbers don't balance, and confirm it's rejected."""
    from solver import PlanStep, SolveResult

    solar, wind, consumers, batteries, grid = _base_inputs()
    bad_step = PlanStep(t=0, batteries={"battery-1": {"charge_kw": 0, "discharge_kw": 0, "soc_pct": 50}}, import_kw=0, export_kw=0)
    bad_result = SolveResult(status="OPTIMAL", objective_value=0, steps=[bad_step] + [PlanStep(t=i) for i in range(1, N_STEPS)], binding_constraints=[], confidence=0.9)

    validation = validate_plan(bad_result, solar=solar, wind=wind, consumers=consumers, batteries=batteries, grid=grid)
    assert not validation.ok
    assert any("power balance" in v for v in validation.violations)
