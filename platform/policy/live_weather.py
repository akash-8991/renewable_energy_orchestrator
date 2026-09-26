"""Live weather feed for `forecast.py` (production-readiness gap: "Forecast
prices are a synthetic sine wave... no live *streaming* feed is wired into
the pipeline"). Uses Open-Meteo's forecast API (https://open-meteo.com) —
free, keyless, no signup — so this is a genuinely real, always-usable
replacement for the synthetic diurnal solar curve and seasonal-naive wind
speed proxy, not a stub behind a paywall a demo can't actually exercise.

Enabled per-tenant from Configuration Studio (`PlatformSettings.
live_weather_enabled` + a site lat/lon); when disabled, missing, or the API
call fails for any reason, callers get `None` back and `forecast.py` falls
back to its synthetic model exactly as before — this is an enhancement
layered on top of the existing baseline, never a hard dependency a decision
cycle can be blocked by.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("reo.live_weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class HourlyWeather:
    cloud_cover_pct: float  # 0-100
    wind_speed_ms: float


@dataclass
class LiveWeatherForecast:
    issued_at: datetime
    by_hour_offset: dict[int, HourlyWeather]  # hours from `issued_at`, floor-rounded

    def at(self, valid_time: datetime) -> HourlyWeather | None:
        offset_hours = round((valid_time - self.issued_at).total_seconds() / 3600)
        return self.by_hour_offset.get(offset_hours)


def fetch_live_weather(lat: float, lon: float, *, timeout_seconds: float = 8.0) -> LiveWeatherForecast | None:
    """A single real HTTP call per decision cycle (the cycle runs every ~10
    minutes; Open-Meteo's own data updates hourly, so this is not
    over-polling a free public service). Returns `None` on any failure —
    network, timeout, unexpected shape — so a weather-API outage degrades to
    the synthetic model instead of ever failing a decision cycle."""
    try:
        import httpx

        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "cloud_cover,wind_speed_10m",
            "wind_speed_unit": "ms",
            "forecast_days": 2,
            "timezone": "UTC",
        }
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        hourly = data["hourly"]
        times = hourly["time"]
        cloud_cover = hourly["cloud_cover"]
        wind_speed = hourly["wind_speed_10m"]

        now = datetime.now(timezone.utc)
        by_hour_offset: dict[int, HourlyWeather] = {}
        for t_str, cc, ws in zip(times, cloud_cover, wind_speed):
            t = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
            offset = round((t - now).total_seconds() / 3600)
            by_hour_offset[offset] = HourlyWeather(cloud_cover_pct=float(cc), wind_speed_ms=float(ws))

        if not by_hour_offset:
            return None
        return LiveWeatherForecast(issued_at=now, by_hour_offset=by_hour_offset)
    except Exception:
        logger.warning("live weather fetch failed — falling back to the synthetic model", exc_info=True)
        return None
