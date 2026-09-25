"""Scenario Lab: forward-looking what-if simulation over the full decision
horizon (hackathon problem 4, solution feature F3 — "simulate situation/
scenario over a period of time by accounting for potential variations in
the input conditions"). This is deliberately distinct from the live
Simulation Lab dashboard page, which injects a shock into the *real-time*
edge-simulator and waits to observe the effect — this module re-solves the
deterministic MILP, entirely offline, against each named scenario's
perturbed inputs, for the *same* horizon the base decision just used, and
persists the comparison before anything happens for real.

Runs BASELINE plus the six named scenarios from scenarios.py — one solve
per scenario. Each solve takes well under a second (see cycle.py's own
timing), so seven solves per 2-minute cycle is not a meaningful cost.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass

from reo_common.models import ScenarioRun
from scenarios import NAMED_SCENARIOS
from solver import BatteryInput, ConsumerInput, GenAssetInput, GridInput, ObjectiveWeights, solve

log = logging.getLogger("optimizer-worker.scenario_lab")


@dataclass
class ScenarioInputs:
    solar: list[GenAssetInput]
    wind: list[GenAssetInput]
    consumers: list[ConsumerInput]
    batteries: list[BatteryInput]
    grid: GridInput


def _perturb(base: ScenarioInputs, scenario_name: str) -> ScenarioInputs:
    shock = NAMED_SCENARIOS.get(scenario_name, {})
    out = deepcopy(base)

    if "solar_multiplier" in shock:
        m = shock["solar_multiplier"]
        for g in out.solar:
            g.forecast_kw = [min(v * m, g.rated_capacity_kw) for v in g.forecast_kw]
    if "wind_multiplier" in shock:
        m = shock["wind_multiplier"]
        for g in out.wind:
            # a >1x multiplier models "wind surge" as if speed rose well
            # into the turbine's rated band — but no asset can exceed its
            # own nameplate rating no matter how strong the wind gets
            g.forecast_kw = [min(v * m, g.rated_capacity_kw) for v in g.forecast_kw]
    if "demand_multiplier" in shock:
        m = shock["demand_multiplier"]
        for c in out.consumers:
            c.forecast_kw = [v * m for v in c.forecast_kw]
    if "price_multiplier" in shock:
        m = shock["price_multiplier"]
        out.grid.price_gbp_per_mwh = [v * m for v in out.grid.price_gbp_per_mwh]
    if "grid_limit_multiplier" in shock:
        out.grid.limit_multiplier = shock["grid_limit_multiplier"]
    if shock.get("battery_available") is False:
        for b in out.batteries:
            b.available = False

    return out


def _summarize(tenant_id: str, decision_cycle_id: str, scenario_name: str, result, step_hours: float, baseline_objective: float | None) -> ScenarioRun:
    total_import = sum(s.import_kw for s in result.steps) * step_hours
    total_export = sum(s.export_kw for s in result.steps) * step_hours
    total_curtailment = sum(sum(s.curtailment.values()) for s in result.steps) * step_hours
    total_shed = sum(sum(s.shed.values()) for s in result.steps) * step_hours

    return ScenarioRun(
        tenant_id=tenant_id,
        decision_cycle_id=decision_cycle_id,
        scenario_name=scenario_name,
        solver_status=result.status,
        objective_value=result.objective_value,
        delta_vs_baseline=(result.objective_value - baseline_objective) if baseline_objective is not None else None,
        total_import_kwh=round(total_import, 1),
        total_export_kwh=round(total_export, 1),
        total_curtailment_kwh=round(total_curtailment, 1),
        total_shed_kwh=round(total_shed, 1),
        binding_constraints=result.binding_constraints,
        confidence=result.confidence,
    )


def run_scenario_comparison(
    *,
    tenant_id: str,
    decision_cycle_id: str,
    n_steps: int,
    step_hours: float,
    base_inputs: ScenarioInputs,
    weights: ObjectiveWeights,
    base_result,  # the cycle's own already-computed SolveResult for the unperturbed case — never re-solved here
) -> list[ScenarioRun]:
    runs: list[ScenarioRun] = [_summarize(tenant_id, decision_cycle_id, "BASELINE", base_result, step_hours, None)]
    baseline_objective = base_result.objective_value

    for scenario_name in NAMED_SCENARIOS:
        try:
            perturbed = _perturb(base_inputs, scenario_name)
            result = solve(
                n_steps=n_steps, step_hours=step_hours, solar=perturbed.solar, wind=perturbed.wind,
                consumers=perturbed.consumers, batteries=perturbed.batteries, grid=perturbed.grid, weights=weights,
            )
            runs.append(_summarize(tenant_id, decision_cycle_id, scenario_name, result, step_hours, baseline_objective))
        except Exception:
            log.exception("scenario %s failed to solve, skipping", scenario_name)

    return runs
