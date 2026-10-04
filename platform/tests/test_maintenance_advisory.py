"""Maintenance is advisory only: the platform raises recommendations for a
human, and no layer can turn one into an executed command."""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "policy"))

from maintenance_advisory import (  # noqa: E402
    ACTION_TYPE, AssetDataHealth, BatteryHealth, maintenance_advisories,
)
from policy.engine.execution import ADVISORY_ONLY_ACTION_TYPES, DISPATCHABLE_ACTION_TYPES, build_signal_for_action  # noqa: E402

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _advise(batteries=(), assets=(), recent=frozenset()):
    return maintenance_advisories(
        tenant_id="t", decision_id="d", now=NOW, batteries=list(batteries), assets=list(assets), recently_raised=set(recent),
    )


def test_a_degraded_battery_raises_an_advisory():
    out = _advise(batteries=[BatteryHealth("b1", "BESS 1", soh_pct=72.0, warranty_cycles_remaining=4000)])
    assert [a.action_type for a in out] == [ACTION_TYPE]
    assert "72%" in out[0].reason and "Advisory only" in out[0].reason


def test_low_warranty_cycles_and_bad_telemetry_each_raise_one():
    out = _advise(
        batteries=[BatteryHealth("b1", "BESS 1", soh_pct=95.0, warranty_cycles_remaining=120)],
        assets=[AssetDataHealth("w1", "Wind 1", "bad"), AssetDataHealth("s1", "Solar 1", "fresh")],
    )
    assert sorted(a.envelope["rule"] for a in out) == ["asset_reporting_bad_data", "battery_warranty_cycles_low"]


def test_healthy_assets_raise_nothing():
    assert _advise(batteries=[BatteryHealth("b1", "BESS 1", 96.0, 5000)], assets=[AssetDataHealth("s1", "Solar 1", "fresh")]) == []


def test_the_same_advisory_is_not_repeated_every_cycle():
    battery = BatteryHealth("b1", "BESS 1", soh_pct=70.0, warranty_cycles_remaining=4000)
    assert _advise(batteries=[battery], recent={("b1", "battery_soh_low")}) == []


def test_an_advisory_needs_no_approval_and_carries_no_setpoint():
    action = _advise(batteries=[BatteryHealth("b1", "BESS 1", 70.0, None)])[0]
    assert action.requires_approval is False
    assert action.quantity == 0.0
    assert action.envelope["advisory_only"] is True
    assert action.risk_level == "low"


def test_no_signal_can_be_built_for_a_maintenance_advice():
    action = _advise(batteries=[BatteryHealth("b1", "BESS 1", 70.0, None)])[0]
    assert "maintenance_advice" in ADVISORY_ONLY_ACTION_TYPES
    assert "maintenance_advice" not in DISPATCHABLE_ACTION_TYPES
    assert build_signal_for_action(None, None, action) is None  # returns before touching the db or decision


def _gateway_module():
    path = Path(__file__).parent.parent / "guardrails" / "ot-gateway-sim" / "main.py"
    spec = importlib.util.spec_from_file_location("ot_gateway_sim_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ot_gateway_refuses_a_maintenance_command_outright():
    gw = _gateway_module()
    req = gw.CommandRequest(
        signal_id="s", tenant_id="t", asset_id="a", command_type="maintenance_advice", setpoint_value=0.0, unit="n/a",
        safety_limits={}, validity_start=NOW.isoformat(), validity_end=NOW.isoformat(), idempotency_key="k", nonce="n",
    )
    ok, reason = gw._independent_validate(req)
    assert ok is False
    assert "advisory only" in reason
