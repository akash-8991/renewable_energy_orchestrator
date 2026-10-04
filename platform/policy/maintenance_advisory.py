"""Maintenance advisories — advisory only.

The platform *recommends* maintenance; it never schedules, dispatches or
enforces it. An advisory is an `Action` row with `action_type =
"maintenance_advice"` that is shown to operators (Action Log, Decision
Centre) and nothing else:

- no Signal is ever built for it (policy/engine/execution.py excludes it from
  DISPATCHABLE_ACTION_TYPES),
- no Approval is created and `requires_approval` is False — there is nothing
  to approve or dispatch, a human decides what to do with the advice,
- the OT gateway refuses a `maintenance_advice` command outright,
- it does not change the optimizer's plan (an operator can separately promote
  an uploaded maintenance notice to a capacity derate — that is an explicit
  human decision, not something this module does).

The rules are deliberately simple, deterministic and explainable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from models.canonical import Action

ACTION_TYPE = "maintenance_advice"
SOH_ADVISORY_BELOW_PCT = 80.0
WARRANTY_CYCLES_ADVISORY_BELOW = 500
REPEAT_AFTER = timedelta(hours=24)  # the same advisory for the same asset is not re-raised every cycle


@dataclass
class BatteryHealth:
    asset_id: str
    name: str
    soh_pct: float
    warranty_cycles_remaining: int | None


@dataclass
class AssetDataHealth:
    asset_id: str
    name: str
    freshness: str  # "bad" only when the source itself flagged the reading bad (not merely old)


def maintenance_advisories(
    *, tenant_id: str, decision_id: str, now: datetime,
    batteries: list[BatteryHealth], assets: list[AssetDataHealth],
    recently_raised: set[tuple[str, str]],
) -> list[Action]:
    """`recently_raised` holds (asset_id, rule) pairs already advised within
    REPEAT_AFTER, which are skipped."""
    found: list[tuple[str, str, str]] = []  # (asset_id, rule, reason)

    for b in batteries:
        if b.soh_pct < SOH_ADVISORY_BELOW_PCT:
            found.append((b.asset_id, "battery_soh_low",
                          f"{b.name}: state of health {b.soh_pct:.0f}% is below {SOH_ADVISORY_BELOW_PCT:.0f}% — "
                          "consider scheduling a capacity test / cell inspection. Advisory only; the platform does not schedule maintenance."))
        if b.warranty_cycles_remaining is not None and b.warranty_cycles_remaining < WARRANTY_CYCLES_ADVISORY_BELOW:
            found.append((b.asset_id, "battery_warranty_cycles_low",
                          f"{b.name}: {b.warranty_cycles_remaining} warranty cycles remaining (below {WARRANTY_CYCLES_ADVISORY_BELOW}) — "
                          "review the warranty position and cycling policy with the OEM. Advisory only."))
    for a in assets:
        if a.freshness == "bad":
            found.append((a.asset_id, "asset_reporting_bad_data",
                          f"{a.name}: latest telemetry is flagged bad by its source — inspect the sensor, "
                          "meter or communications link. Advisory only; the platform does not dispatch maintenance."))

    actions = []
    for asset_id, rule, reason in found:
        if (asset_id, rule) in recently_raised:
            continue
        actions.append(Action(
            tenant_id=tenant_id, decision_id=decision_id, asset_id=asset_id, action_type=ACTION_TYPE,
            quantity=0.0, unit="n/a", start_time=now, end_time=now + REPEAT_AFTER,
            envelope={"advisory_only": True, "rule": rule}, expiry=None,
            risk_level="low", reason=reason, expected_outcome={}, requires_approval=False,
        ))
    return actions
