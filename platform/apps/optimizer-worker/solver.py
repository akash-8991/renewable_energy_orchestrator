"""Deterministic rolling-horizon MILP optimizer (TRD §4, ADR-001: the
optimizer — never an LLM/agent — is the sole dispatch authority).

OR-Tools CBC (open source, no license cost — satisfies "solver abstraction
supports OSS+commercial"). Decision variables and hard constraints follow
TRD §4's list; the multi-objective combination is weighted-sum over the
tenant's ObjectivePolicy weights, with every *safety* requirement expressed
as a hard MILP constraint rather than a penalty term — which is what makes
BR-01 ("safety and hard technical constraints always dominate economic
objectives") true structurally, not just by convention.

Simplifications kept deliberately small and named here (see
docs/SIMPLIFICATIONS.md for the full list): no explicit ramp-rate or
spinning-reserve constraints yet (extension point noted below); grid
carbon intensity is a fixed constant rather than a live marginal-emissions
feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.linear_solver import pywraplp

GRID_CARBON_INTENSITY_KG_PER_KWH = 0.233  # illustrative GB grid average; a real deployment feeds this live


@dataclass
class BatteryInput:
    asset_id: str
    power_limit_kw: float
    energy_capacity_kwh: float
    soc_min_pct: float
    soc_max_pct: float
    initial_soc_pct: float
    round_trip_efficiency: float
    degradation_cost_per_kwh: float
    available: bool = True


@dataclass
class GenAssetInput:
    asset_id: str
    forecast_kw: list[float]  # len == n_steps, non-negative available generation


@dataclass
class ConsumerInput:
    asset_id: str
    forecast_kw: list[float]
    max_shed_frac: float = 0.15
    shed_penalty_per_kwh: float = 5.0


@dataclass
class GridInput:
    asset_id: str
    price_gbp_per_mwh: list[float]
    max_import_kw: float
    max_export_kw: float
    limit_multiplier: float = 1.0  # < 1.0 to model line congestion


@dataclass
class ObjectiveWeights:
    cost: float = 0.35
    degradation: float = 0.1
    carbon: float = 0.15
    curtailment: float = 0.15
    reliability: float = 0.1
    carbon_price_per_tonne: float = 80.0


@dataclass
class PlanStep:
    t: int
    batteries: dict[str, dict] = field(default_factory=dict)  # asset_id -> {charge_kw, discharge_kw, soc_pct}
    curtailment: dict[str, float] = field(default_factory=dict)  # asset_id -> curtailed_kw
    shed: dict[str, float] = field(default_factory=dict)  # asset_id -> shed_kw
    import_kw: float = 0.0
    export_kw: float = 0.0


@dataclass
class SolveResult:
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | ERROR
    objective_value: float
    steps: list[PlanStep]
    binding_constraints: list[str]
    confidence: float


def solve(
    *,
    n_steps: int,
    step_hours: float,
    solar: list[GenAssetInput],
    wind: list[GenAssetInput],
    consumers: list[ConsumerInput],
    batteries: list[BatteryInput],
    grid: GridInput,
    weights: ObjectiveWeights,
) -> SolveResult:
    solver = pywraplp.Solver.CreateSolver("CBC")
    if solver is None:
        return SolveResult(status="ERROR", objective_value=0.0, steps=[], binding_constraints=["solver unavailable"], confidence=0.0)

    inf = solver.infinity()
    T = range(n_steps)

    charge = {(b.asset_id, t): solver.NumVar(0, b.power_limit_kw if b.available else 0, f"charge_{b.asset_id}_{t}") for b in batteries for t in T}
    discharge = {(b.asset_id, t): solver.NumVar(0, b.power_limit_kw if b.available else 0, f"discharge_{b.asset_id}_{t}") for b in batteries for t in T}
    soc = {(b.asset_id, t): solver.NumVar(b.soc_min_pct, b.soc_max_pct, f"soc_{b.asset_id}_{t}") for b in batteries for t in T}
    charge_mode = {(b.asset_id, t): solver.IntVar(0, 1, f"mode_{b.asset_id}_{t}") for b in batteries for t in T}

    curtail = {(g.asset_id, t): solver.NumVar(0, max(g.forecast_kw[t], 0.0), f"curtail_{g.asset_id}_{t}") for g in solar + wind for t in T}
    shed = {(c.asset_id, t): solver.NumVar(0, max(c.forecast_kw[t], 0.0) * c.max_shed_frac, f"shed_{c.asset_id}_{t}") for c in consumers for t in T}

    max_import = grid.max_import_kw * grid.limit_multiplier
    max_export = grid.max_export_kw * grid.limit_multiplier
    grid_import = {t: solver.NumVar(0, max_import, f"import_{t}") for t in T}
    grid_export = {t: solver.NumVar(0, max_export, f"export_{t}") for t in T}
    grid_mode = {t: solver.IntVar(0, 1, f"grid_mode_{t}") for t in T}

    # --- hard constraints -------------------------------------------------
    for b in batteries:
        for t in T:
            # mutually exclusive charge/discharge (TRD §4 "mutually-exclusive states")
            solver.Add(charge[b.asset_id, t] <= (b.power_limit_kw if b.available else 0) * charge_mode[b.asset_id, t])
            solver.Add(discharge[b.asset_id, t] <= (b.power_limit_kw if b.available else 0) * (1 - charge_mode[b.asset_id, t]))

            prev_soc = b.initial_soc_pct if t == 0 else soc[b.asset_id, t - 1]
            if b.energy_capacity_kwh > 0:
                delta_pct = (
                    (charge[b.asset_id, t] * b.round_trip_efficiency - discharge[b.asset_id, t] / b.round_trip_efficiency)
                    * step_hours / b.energy_capacity_kwh * 100
                )
            else:
                delta_pct = 0
            solver.Add(soc[b.asset_id, t] == prev_soc + delta_pct)

    for t in T:
        solver.Add(grid_import[t] <= max_import * grid_mode[t])
        solver.Add(grid_export[t] <= max_export * (1 - grid_mode[t]))

        gen_terms = sum(g.forecast_kw[t] - curtail[g.asset_id, t] for g in solar + wind)
        battery_net = sum(discharge[b.asset_id, t] - charge[b.asset_id, t] for b in batteries)
        demand_terms = sum(c.forecast_kw[t] - shed[c.asset_id, t] for c in consumers)
        # power balance (TRD §4 "nodal/portfolio power balance") — the one
        # constraint every other hard limit exists to keep feasible
        solver.Add(gen_terms + battery_net + grid_import[t] - grid_export[t] == demand_terms)

    # --- objective ----------------------------------------------------
    objective_terms = []
    for t in T:
        price = grid.price_gbp_per_mwh[t]
        energy_scale = step_hours  # kWh per kW of power over this step
        objective_terms.append(weights.cost * grid_import[t] * energy_scale * price / 1000.0)
        objective_terms.append(-weights.cost * grid_export[t] * energy_scale * price / 1000.0)
        objective_terms.append(
            weights.carbon * grid_import[t] * energy_scale * GRID_CARBON_INTENSITY_KG_PER_KWH / 1000.0 * weights.carbon_price_per_tonne
        )
        for b in batteries:
            objective_terms.append(
                weights.degradation * (charge[b.asset_id, t] + discharge[b.asset_id, t]) * energy_scale * b.degradation_cost_per_kwh
            )
        for g in solar + wind:
            objective_terms.append(weights.curtailment * curtail[g.asset_id, t] * energy_scale * 20.0)  # illustrative curtailment penalty GBP/kWh
        for c in consumers:
            objective_terms.append(weights.reliability * shed[c.asset_id, t] * energy_scale * c.shed_penalty_per_kwh)

    solver.Minimize(solver.Sum(objective_terms))

    status = solver.Solve()
    status_name = {
        pywraplp.Solver.OPTIMAL: "OPTIMAL",
        pywraplp.Solver.FEASIBLE: "FEASIBLE",
        pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
        pywraplp.Solver.UNBOUNDED: "ERROR",
        pywraplp.Solver.ABNORMAL: "ERROR",
        pywraplp.Solver.NOT_SOLVED: "ERROR",
    }.get(status, "ERROR")

    if status_name not in ("OPTIMAL", "FEASIBLE"):
        return SolveResult(status=status_name, objective_value=0.0, steps=[], binding_constraints=["infeasible under current constraints"], confidence=0.0)

    steps: list[PlanStep] = []
    binding: set[str] = set()
    for t in T:
        step = PlanStep(t=t, import_kw=grid_import[t].solution_value(), export_kw=grid_export[t].solution_value())
        for b in batteries:
            soc_val = soc[b.asset_id, t].solution_value()
            step.batteries[b.asset_id] = {
                "charge_kw": round(charge[b.asset_id, t].solution_value(), 3),
                "discharge_kw": round(discharge[b.asset_id, t].solution_value(), 3),
                "soc_pct": round(soc_val, 3),
            }
            if soc_val <= b.soc_min_pct + 0.5 or soc_val >= b.soc_max_pct - 0.5:
                binding.add(f"battery:{b.asset_id}:soc_bound")
        for g in solar + wind:
            c_val = curtail[g.asset_id, t].solution_value()
            if c_val > 0.01:
                step.curtailment[g.asset_id] = round(c_val, 3)
        for c in consumers:
            s_val = shed[c.asset_id, t].solution_value()
            if s_val > 0.01:
                step.shed[c.asset_id] = round(s_val, 3)
        if grid_import[t].solution_value() >= max_import - 1.0:
            binding.add("grid:import_limit")
        if grid_export[t].solution_value() >= max_export - 1.0:
            binding.add("grid:export_limit")
        steps.append(step)

    confidence = 0.92 if status_name == "OPTIMAL" else 0.55
    return SolveResult(
        status=status_name, objective_value=solver.Objective().Value(), steps=steps,
        binding_constraints=sorted(binding), confidence=confidence,
    )
