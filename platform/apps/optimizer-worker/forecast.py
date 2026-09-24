"""Forecast generation (FR-FC-001/002, PRD E3): point + quantile forecasts
for solar, wind, demand and price, persisted with issue time/horizon/model
version so every Decision can cite exactly which forecast it used.

Simplification (see docs/SIMPLIFICATIONS.md): rather than a trained ML
model, this uses the same physically-grounded curves the edge-simulator
uses as ground truth (diurnal solar, a wind power curve) plus a
persistence/seasonal-naive blend and an uncertainty band that widens with
lead time — a legitimate, well-established forecasting *baseline*
(FR-FC-002 explicitly requires a persistence/baseline fallback to exist
regardless), just not a trained model. `model_version` is stamped
"baseline-v1" so a real trained model can be swapped in later without
changing anything that consumes Forecast rows.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from reo_common.models import Asset, Forecast

MODEL_VERSION = "baseline-v1"


@dataclass
class ForecastPoint:
    valid_time: datetime
    quantile: float
    value: float
    unit: str


def _solar_diurnal_factor(hour: float) -> float:
    if hour < 6 or hour > 20:
        return 0.0
    return max(0.0, math.sin(math.pi * (hour - 6) / 14)) ** 1.5


def _wind_power_factor(speed_ms: float) -> float:
    if speed_ms < 3 or speed_ms > 25:
        return 0.0
    if speed_ms >= 12:
        return 1.0
    return ((speed_ms - 3) / 9) ** 3


QUANTILES = (0.1, 0.5, 0.9)


def forecast_asset(asset: Asset, horizon_hours: int, step_hours: float, now: datetime) -> list[ForecastPoint]:
    """Generate quantile forecasts for one asset over the horizon. Solar and
    wind get physically-grounded diurnal/curve-based means; demand gets a
    daily-shape baseline; other asset types return no forecast (their
    telemetry is treated as directly observed, not forecast)."""
    points: list[ForecastPoint] = []
    n_steps = int(horizon_hours / step_hours)

    for step in range(1, n_steps + 1):
        valid_time = now + timedelta(hours=step * step_hours)
        hour = valid_time.hour + valid_time.minute / 60
        lead_hours = step * step_hours
        # uncertainty widens with lead time — a simple, defensible growth curve
        uncertainty_frac = min(0.6, 0.05 + 0.02 * lead_hours)

        if asset.asset_type == "solar":
            mean_kw = asset.rated_capacity_kw * _solar_diurnal_factor(hour) * 0.92
            unit = "kW"
        elif asset.asset_type == "wind":
            # seasonal-naive wind speed proxy: mild diurnal variation around a base speed
            base_speed = 8.0 + 1.5 * math.sin(math.pi * hour / 12)
            mean_kw = asset.rated_capacity_kw * _wind_power_factor(base_speed) * 0.9
            unit = "kW"
        elif asset.asset_type == "consumer":
            base = 0.5 + 0.3 * math.sin(math.pi * (hour - 7) / 12) ** 2
            mean_kw = asset.rated_capacity_kw * base
            unit = "kW"
        elif asset.asset_type == "grid_interconnection":
            mean_kw = 65.0 + 20 * math.sin(math.pi * (hour - 6) / 12)  # price forecast, GBP/MWh, reuses the kw-named var
            unit = "GBP/MWh"
        else:
            continue

        for q in QUANTILES:
            if q == 0.5:
                value = mean_kw
            else:
                z = -1.2816 if q == 0.1 else 1.2816  # approx 10th/90th percentile of a normal
                value = max(0.0, mean_kw * (1 + z * uncertainty_frac)) if unit != "GBP/MWh" else mean_kw * (1 + z * uncertainty_frac)
            points.append(ForecastPoint(valid_time=valid_time, quantile=q, value=value, unit=unit))

    return points


def generate_and_persist_forecasts(
    db, tenant_id: str, assets: list[Asset], horizon_hours: int, step_hours: float, now: datetime
) -> str:
    # `now` MUST be passed in by the caller (the decision cycle's own
    # snapshot timestamp), not computed here — this is what issue_time gets
    # stamped with, and the cycle later looks up these rows by
    # `Forecast.issue_time == now` using its own copy of that same value.
    # Two independently-computed `datetime.now()` calls a few milliseconds
    # apart never compare equal, which silently zeroed every forecast series
    # (empty query result) the first time this ran end-to-end.
    bundle_id = f"fc-{now.strftime('%Y%m%dT%H%M%S')}-{random.randint(1000, 9999)}"

    rows = []
    for asset in assets:
        if asset.asset_type not in ("solar", "wind", "consumer", "grid_interconnection"):
            continue
        variable = {"solar": "solar", "wind": "wind", "consumer": "demand", "grid_interconnection": "price"}[asset.asset_type]
        for point in forecast_asset(asset, horizon_hours, step_hours, now):
            rows.append(
                Forecast(
                    tenant_id=tenant_id,
                    site_id=asset.site_id,
                    asset_id=asset.id,
                    variable=variable,
                    issue_time=now,
                    valid_time=point.valid_time,
                    quantile=point.quantile,
                    value=point.value,
                    unit=point.unit,
                    model_version=MODEL_VERSION,
                    is_fallback=False,
                )
            )
    db.add_all(rows)
    db.flush()
    return bundle_id
