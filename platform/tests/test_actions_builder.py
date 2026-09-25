"""Every action family the hackathon problem statement's taxonomy names
(battery charge/discharge, grid buy/sell, renewable curtailment, demand
response) must become a governed Action, not just a number buried in
Decision.plan JSON. Exercises actions_builder.py directly with synthetic
data — no DB, no live simulator timing dependency (this used to only be
verifiable by waiting for the right real-world hour to hit each condition
naturally, which is exactly the kind of thing that should be a fast
deterministic test instead)."""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "policy"))

from actions_builder import AssetRef, build_actions_for_step  # noqa: E402
from solver import PlanStep  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
GRID = AssetRef(id="grid-1", rated_capacity_kw=120_000)


def _common_kwargs(**overrides):
    kwargs = dict(
        tenant_id="tenant-1", decision_id="dec-1", binding_constraints=[], now=NOW, step_hours=1.0,
        approval_window_minutes=15, asset_by_id={"battery-1": AssetRef(id="battery-1", rated_capacity_kw=1000),
                                                  "solar-1": AssetRef(id="solar-1", rated_capacity_kw=40000),
                                                  "wind-1": AssetRef(id="wind-1", rated_capacity_kw=30000),
                                                  "consumer-1": AssetRef(id="consumer-1", rated_capacity_kw=6000)},
        grid_asset=GRID, grid_max_import_kw=120000, grid_max_export_kw=100000,
    )
    kwargs.update(overrides)
    return kwargs


def test_battery_charge_becomes_governed_action():
    step = PlanStep(t=0, batteries={"battery-1": {"charge_kw": 500.0, "discharge_kw": 0.0, "soc_pct": 60.0}})
    actions = build_actions_for_step(step, **_common_kwargs())
    assert len(actions) == 1
    assert actions[0].action_type == "charge"
    assert actions[0].quantity == 500.0
    assert actions[0].asset_id == "battery-1"


def test_battery_discharge_becomes_governed_action():
    step = PlanStep(t=0, batteries={"battery-1": {"charge_kw": 0.0, "discharge_kw": 300.0, "soc_pct": 40.0}})
    actions = build_actions_for_step(step, **_common_kwargs())
    assert len(actions) == 1
    assert actions[0].action_type == "discharge"


def test_grid_import_becomes_buy_action():
    step = PlanStep(t=0, import_kw=15000.0, export_kw=0.0)
    actions = build_actions_for_step(step, **_common_kwargs())
    assert len(actions) == 1
    assert actions[0].action_type == "buy"
    assert actions[0].asset_id == GRID.id
    assert actions[0].envelope["max_kw"] == 120000


def test_grid_export_becomes_sell_action():
    step = PlanStep(t=0, import_kw=0.0, export_kw=22000.0)
    actions = build_actions_for_step(step, **_common_kwargs())
    assert len(actions) == 1
    assert actions[0].action_type == "sell"
    assert actions[0].envelope["max_kw"] == 100000


def test_curtailment_becomes_governed_action_per_asset():
    step = PlanStep(t=0, curtailment={"solar-1": 5000.0, "wind-1": 2000.0})
    actions = build_actions_for_step(step, **_common_kwargs())
    assert {a.asset_id for a in actions} == {"solar-1", "wind-1"}
    assert all(a.action_type == "curtail" for a in actions)


def test_demand_response_becomes_governed_action():
    step = PlanStep(t=0, shed={"consumer-1": 800.0})
    actions = build_actions_for_step(step, **_common_kwargs())
    assert len(actions) == 1
    assert actions[0].action_type == "demand_response"
    assert actions[0].envelope["max_kw"] == 6000 * 0.15


def test_sub_threshold_values_produce_no_action():
    step = PlanStep(t=0, import_kw=0.5, export_kw=0.0, curtailment={"solar-1": 0.9}, shed={"consumer-1": 0.1})
    actions = build_actions_for_step(step, **_common_kwargs())
    assert actions == []


def test_a_full_coordinated_cycle_produces_one_action_per_family():
    """F1 ("determine optimal cluster of actions"): a single step can and
    should produce several simultaneous, distinct governed actions — not
    just one action type at a time."""
    step = PlanStep(
        t=0,
        batteries={"battery-1": {"charge_kw": 400.0, "discharge_kw": 0.0, "soc_pct": 55.0}},
        curtailment={"solar-1": 1200.0},
        shed={"consumer-1": 500.0},
        import_kw=0.0, export_kw=8000.0,
    )
    actions = build_actions_for_step(step, **_common_kwargs())
    action_types = {a.action_type for a in actions}
    assert action_types == {"charge", "sell", "curtail", "demand_response"}
