"""Shocks are deterministic named scenarios, not Monte Carlo trajectories
(policy/scenarios.py's design rule). These tests fail if someone adds random
sampling to the scenario path or lets a scenario's outcome vary between
identical runs."""

import ast
import sys
from pathlib import Path

POLICY_DIR = Path(__file__).parent.parent / "policy"
sys.path.insert(0, str(POLICY_DIR))

from scenario_lab import ScenarioInputs, run_scenario_comparison  # noqa: E402
from scenarios import NAMED_SCENARIOS  # noqa: E402
from solver import BatteryInput, ConsumerInput, GenAssetInput, GridInput, ObjectiveWeights, solve  # noqa: E402

EXPECTED = {"CLOUD_COVER", "WIND_SURGE", "PRICE_SPIKE", "BATTERY_OUTAGE", "LINE_CONGESTION", "DEMAND_SHOCK"}


def test_the_named_scenario_set_is_exactly_the_six_documented_shocks():
    assert set(NAMED_SCENARIOS) == EXPECTED


def test_every_shock_is_a_fixed_constant_not_a_distribution():
    for name, shock in NAMED_SCENARIOS.items():
        assert shock, name
        for value in shock.values():
            assert isinstance(value, (int, float, bool)), f"{name}: {value!r} is not a fixed value"


def _imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def test_scenario_modules_do_not_sample_randomly():
    for module in ("scenarios.py", "scenario_lab.py"):
        imported = _imported_names(POLICY_DIR / module)
        offenders = {n for n in imported if n == "random" or n.startswith(("random.", "numpy.random", "scipy.stats"))}
        assert not offenders, f"{module} imports random sampling: {offenders}"


def _inputs() -> tuple[int, ScenarioInputs, ObjectiveWeights]:
    n = 6
    solar = [GenAssetInput(asset_id="s1", forecast_kw=[0, 200, 800, 1000, 600, 100], rated_capacity_kw=1000)]
    wind = [GenAssetInput(asset_id="w1", forecast_kw=[300] * n, rated_capacity_kw=900)]
    consumers = [ConsumerInput(asset_id="c1", forecast_kw=[500, 500, 600, 700, 700, 600])]
    batteries = [BatteryInput(
        asset_id="b1", power_limit_kw=400, energy_capacity_kwh=1600, soc_min_pct=10, soc_max_pct=95,
        initial_soc_pct=50, round_trip_efficiency=0.92, degradation_cost_per_kwh=0.02,
    )]
    grid = GridInput(asset_id="g1", price_gbp_per_mwh=[60, 55, 70, 90, 120, 80], max_import_kw=2000, max_export_kw=2000)
    return n, ScenarioInputs(solar=solar, wind=wind, consumers=consumers, batteries=batteries, grid=grid), ObjectiveWeights()


def _run():
    n, inputs, weights = _inputs()
    base = solve(n_steps=n, step_hours=1.0, solar=inputs.solar, wind=inputs.wind, consumers=inputs.consumers,
                 batteries=inputs.batteries, grid=inputs.grid, weights=weights)
    runs = run_scenario_comparison(
        tenant_id="t", decision_cycle_id="c", n_steps=n, step_hours=1.0, base_inputs=inputs, weights=weights, base_result=base,
    )
    return [(r.scenario_name, r.solver_status, r.objective_value, r.total_import_kwh, r.total_curtailment_kwh, r.total_shed_kwh) for r in runs]


def test_scenario_comparison_is_one_baseline_plus_each_named_shock_once():
    names = [row[0] for row in _run()]
    assert names == ["BASELINE", *NAMED_SCENARIOS]


def test_identical_inputs_give_identical_scenario_outcomes():
    assert _run() == _run()
