"""Forecast generation (FR-FC-001/002, PRD E3): point + quantile forecasts
for solar, wind, demand and price, persisted with issue time/horizon/model
version so every Decision can cite exactly which forecast it used.

Two forecasters, chosen by the operator's forecast criteria
(reo_common/forecast_ml.py):

- the **physics-based baseline** — the same physically-grounded curves the
  edge-simulator uses as ground truth (diurnal solar, a wind power curve,
  daily demand/price shapes) with an uncertainty band that widens with lead
  time. Stamped "baseline-v1". FR-FC-002 requires this baseline to exist
  regardless, and it is what runs until an operator accepts or sets criteria.
- a **trained ML model** per asset (ridge regression on time features with
  the physics baseline as an input, band calibrated from held-out residuals),
  trained on the telemetry the platform has ingested. Stamped "ml-ridge-v1".
  Used per asset when the criteria allow it and the model qualifies.

Every Forecast row carries its model_version, so a Decision can cite exactly
which forecaster produced each point.

When a tenant enables the live weather feed (Configuration Studio ->
`PlatformSettings.live_weather_enabled` + a site lat/lon), `live_weather.
fetch_live_weather()` supplies real Open-Meteo cloud-cover/wind-speed data
for the horizon, and solar/wind points are derated/computed from that real
data instead of the synthetic curves — stamped "live-weather-v1" so a
Decision can tell which forecast points actually used live data versus the
synthetic baseline. A weather-API failure (or the feed being disabled)
falls back to the synthetic model with no special-casing by the caller.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from models.canonical import Asset, Forecast
from reo_common.forecast_ml import (
    ML_MODEL_VERSION, PHYSICS_MODEL_VERSION, VARIABLES, EffectiveCriteria, TrainedModel,
    solar_diurnal_factor, wind_power_factor,
)

from live_weather import LiveWeatherForecast

MODEL_VERSION = PHYSICS_MODEL_VERSION
LIVE_WEATHER_MODEL_VERSION = "live-weather-v1"


@dataclass
class ForecastPoint:
    valid_time: datetime
    quantile: float
    value: float
    unit: str
    used_live_weather: bool = False
    used_ml: bool = False


# the physics curves live in reo_common.forecast_ml (shared with the ML trainer, which builds on them)
_solar_diurnal_factor = solar_diurnal_factor
_wind_power_factor = wind_power_factor


QUANTILES = (0.1, 0.5, 0.9)


def forecast_asset(
    asset: Asset, horizon_hours: int, step_hours: float, now: datetime,
    weather: LiveWeatherForecast | None = None,
    ml_model: TrainedModel | None = None, criteria: EffectiveCriteria | None = None,
) -> list[ForecastPoint]:
    """Generate quantile forecasts for one asset over the horizon. Solar and
    wind get physically-grounded diurnal/curve-based means (or, when `weather`
    is supplied, real cloud-cover/wind-speed data for that hour); demand gets
    a daily-shape baseline; other asset types return no forecast (their
    telemetry is treated as directly observed, not forecast)."""
    points: list[ForecastPoint] = []
    n_steps = int(horizon_hours / step_hours)

    for step in range(1, n_steps + 1):
        valid_time = now + timedelta(hours=step * step_hours)
        hour = valid_time.hour + valid_time.minute / 60
        lead_hours = step * step_hours
        # uncertainty widens with lead time — a simple, defensible growth curve
        band_scale = criteria.band_scale if criteria else 1.0  # operator-set: wider band = more conservative planning
        uncertainty_frac = min(0.6, 0.05 + 0.02 * lead_hours) * band_scale
        hourly_weather = weather.at(valid_time) if weather else None
        used_live_weather = False

        if asset.asset_type == "solar":
            if hourly_weather is not None:
                # real forecasted cloud cover derates the same physically-
                # required day/night diurnal shape (cloud cover alone can't
                # tell day from night) — a lighter, more realistic overcast
                # penalty than a flat linear one, since even heavy cloud
                # still passes diffuse irradiance.
                clear_sky_factor = 1 - 0.75 * (hourly_weather.cloud_cover_pct / 100) ** 1.5
                mean_kw = asset.rated_capacity_kw * _solar_diurnal_factor(hour) * clear_sky_factor * 0.92
                used_live_weather = True
            else:
                mean_kw = asset.rated_capacity_kw * _solar_diurnal_factor(hour) * 0.92
            unit = "kW"
        elif asset.asset_type == "wind":
            if hourly_weather is not None:
                mean_kw = asset.rated_capacity_kw * _wind_power_factor(hourly_weather.wind_speed_ms) * 0.9
                used_live_weather = True
            else:
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

        # trained ML model (when the operator's criteria let this asset use
        # one): the physics mean above is its input feature, its output
        # replaces the mean, and its band comes from its own held-out residuals
        used_ml = ml_model is not None
        if used_ml:
            ml_mean = ml_model.predict_mean(valid_time, mean_kw)
            lower, upper = ml_model.band(valid_time, lead_hours, band_scale)
            lower, upper = min(lower, 0.0), max(upper, 0.0)
            floor = None if unit == "GBP/MWh" else 0.0
            quantile_values = {0.1: ml_mean + lower, 0.5: ml_mean, 0.9: ml_mean + upper}
            if floor is not None:
                quantile_values = {q: max(floor, v) for q, v in quantile_values.items()}
        else:
            quantile_values = {}

        for q in QUANTILES:
            if used_ml:
                value = quantile_values[q]
            elif q == 0.5:
                value = mean_kw
            else:
                z = -1.2816 if q == 0.1 else 1.2816  # approx 10th/90th percentile of a normal
                value = max(0.0, mean_kw * (1 + z * uncertainty_frac)) if unit != "GBP/MWh" else mean_kw * (1 + z * uncertainty_frac)
            points.append(ForecastPoint(valid_time=valid_time, quantile=q, value=value, unit=unit,
                                        used_live_weather=used_live_weather and not used_ml, used_ml=used_ml))

    return points


def generate_and_persist_forecasts(
    db, tenant_id: str, assets: list[Asset], horizon_hours: int, step_hours: float, now: datetime,
    weather: LiveWeatherForecast | None = None,
    ml_models: dict[str, TrainedModel] | None = None, criteria: EffectiveCriteria | None = None,
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
        # an explicit "ml" mode with no usable model for this asset is a fallback
        # to the baseline (FR-FC-002) — recorded so it is visible, not silent
        fell_back = bool(criteria and criteria.model_mode == "ml" and asset.id not in (ml_models or {}))
        for point in forecast_asset(asset, horizon_hours, step_hours, now, weather, (ml_models or {}).get(asset.id), criteria):
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
                    model_version=ML_MODEL_VERSION if point.used_ml else (LIVE_WEATHER_MODEL_VERSION if point.used_live_weather else MODEL_VERSION),
                    is_fallback=fell_back,
                )
            )
    db.add_all(rows)
    db.flush()
    return bundle_id
