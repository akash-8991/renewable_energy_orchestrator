"""Independent feasibility validator (BR-03, TRD §4: "independently
implemented constraint subset" re-checks every optimizer action before it
can reach the policy/safety layer).

Deliberately does NOT import anything from `solver.py` or reuse its
internal MILP state — it only looks at the plain numbers in a `SolveResult`
and recomputes each check from first principles. The point of this
separation is that a bug in the solver's constraint *encoding* (as opposed
to a genuine infeasibility) would still be caught here, because this module
would have to have the identical bug for it to slip through both.
"""

from __future__ import annotations

from dataclasses import dataclass

from policy.solver import BatteryInput, ConsumerInput, GenAssetInput, GridInput, SolveResult

BALANCE_TOLERANCE_KW = 5.0
BOUND_TOLERANCE = 0.5


@dataclass
class ValidationResult:
    ok: bool
    violations: list[str]


def validate_plan(
    result: SolveResult,
    *,
    solar: list[GenAssetInput],
    wind: list[GenAssetInput],
    consumers: list[ConsumerInput],
    batteries: list[BatteryInput],
    grid: GridInput,
) -> ValidationResult:
    violations: list[str] = []

    if result.status not in ("OPTIMAL", "FEASIBLE"):
        return ValidationResult(ok=False, violations=[f"solver status {result.status}"])

    battery_by_id = {b.asset_id: b for b in batteries}

    for step in result.steps:
        t = step.t

        gen_total = sum(g.forecast_kw[t] - step.curtailment.get(g.asset_id, 0.0) for g in solar + wind)
        battery_net = sum(v["discharge_kw"] - v["charge_kw"] for v in step.batteries.values())
        demand_total = sum(c.forecast_kw[t] - step.shed.get(c.asset_id, 0.0) for c in consumers)
        balance_error = abs(gen_total + battery_net + step.import_kw - step.export_kw - demand_total)
        if balance_error > BALANCE_TOLERANCE_KW:
            violations.append(f"t={t}: power balance error {balance_error:.2f}kW exceeds tolerance")

        for asset_id, v in step.batteries.items():
            b = battery_by_id.get(asset_id)
            if b is None:
                violations.append(f"t={t}: unknown battery {asset_id} in plan")
                continue
            if v["charge_kw"] > b.power_limit_kw + BOUND_TOLERANCE:
                violations.append(f"t={t}: battery {asset_id} charge {v['charge_kw']:.1f}kW exceeds power limit {b.power_limit_kw}kW")
            if v["discharge_kw"] > b.power_limit_kw + BOUND_TOLERANCE:
                violations.append(f"t={t}: battery {asset_id} discharge {v['discharge_kw']:.1f}kW exceeds power limit {b.power_limit_kw}kW")
            if v["charge_kw"] > BOUND_TOLERANCE and v["discharge_kw"] > BOUND_TOLERANCE:
                violations.append(f"t={t}: battery {asset_id} charges and discharges simultaneously")
            if not (b.soc_min_pct - BOUND_TOLERANCE <= v["soc_pct"] <= b.soc_max_pct + BOUND_TOLERANCE):
                violations.append(f"t={t}: battery {asset_id} SoC {v['soc_pct']:.1f}% outside [{b.soc_min_pct},{b.soc_max_pct}]")
            if not b.available and (v["charge_kw"] > BOUND_TOLERANCE or v["discharge_kw"] > BOUND_TOLERANCE):
                violations.append(f"t={t}: battery {asset_id} is under outage but plan uses it")

        if step.import_kw > grid.max_import_kw * grid.limit_multiplier + BOUND_TOLERANCE:
            violations.append(f"t={t}: grid import {step.import_kw:.1f}kW exceeds limit")
        if step.export_kw > grid.max_export_kw * grid.limit_multiplier + BOUND_TOLERANCE:
            violations.append(f"t={t}: grid export {step.export_kw:.1f}kW exceeds limit")
        if step.import_kw > BOUND_TOLERANCE and step.export_kw > BOUND_TOLERANCE:
            violations.append(f"t={t}: simultaneous grid import and export")

        for c in consumers:
            shed_val = step.shed.get(c.asset_id, 0.0)
            cap = c.forecast_kw[t] * c.max_shed_frac
            if shed_val > cap + BOUND_TOLERANCE:
                violations.append(f"t={t}: consumer {c.asset_id} shed {shed_val:.1f}kW exceeds cap {cap:.1f}kW")

        for g in solar + wind:
            curtail_val = step.curtailment.get(g.asset_id, 0.0)
            if curtail_val > g.forecast_kw[t] + BOUND_TOLERANCE:
                violations.append(f"t={t}: asset {g.asset_id} curtailment exceeds available generation")

    return ValidationResult(ok=len(violations) == 0, violations=violations)
