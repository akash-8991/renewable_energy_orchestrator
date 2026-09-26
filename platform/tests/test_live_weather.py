"""Live weather feed (production-readiness gap: forecasts were a pure
synthetic sine wave, no live feed wired in). `fetch_live_weather` must
never raise — a weather-API outage degrades to `None` (the caller's
synthetic fallback), never a failed decision cycle — and `forecast_asset`
must use real cloud-cover/wind-speed data when a `LiveWeatherForecast` is
supplied, tagging those points so a Decision can tell which model produced
them."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "policy"))

from live_weather import HourlyWeather, LiveWeatherForecast, fetch_live_weather  # noqa: E402
from forecast import LIVE_WEATHER_MODEL_VERSION, MODEL_VERSION, forecast_asset, generate_and_persist_forecasts  # noqa: E402


class _FakeAsset:
    def __init__(self, asset_type: str, rated_capacity_kw: float = 10_000, id: str = "a1", site_id: str = "s1"):
        self.asset_type = asset_type
        self.rated_capacity_kw = rated_capacity_kw
        self.id = id
        self.site_id = site_id


NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _flat_weather(cloud_cover_pct: float, wind_speed_ms: float, hours: int = 24) -> LiveWeatherForecast:
    return LiveWeatherForecast(
        issued_at=NOW,
        by_hour_offset={h: HourlyWeather(cloud_cover_pct=cloud_cover_pct, wind_speed_ms=wind_speed_ms) for h in range(hours + 1)},
    )


def test_fetch_live_weather_returns_none_on_network_failure(monkeypatch):
    import httpx

    class _BrokenClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "Client", _BrokenClient)
    assert fetch_live_weather(51.5, -0.1) is None


def test_hourly_weather_lookup_by_offset():
    weather = _flat_weather(cloud_cover_pct=20, wind_speed_ms=9)
    point = weather.at(NOW + timedelta(hours=3))
    assert point is not None
    assert point.wind_speed_ms == 9


def test_hourly_weather_lookup_miss_returns_none():
    weather = _flat_weather(cloud_cover_pct=20, wind_speed_ms=9, hours=2)
    assert weather.at(NOW + timedelta(hours=50)) is None


def test_clear_sky_solar_beats_heavy_cloud_cover():
    asset = _FakeAsset("solar")
    clear = forecast_asset(asset, 24, 1.0, NOW, _flat_weather(cloud_cover_pct=0, wind_speed_ms=5))
    cloudy = forecast_asset(asset, 24, 1.0, NOW, _flat_weather(cloud_cover_pct=100, wind_speed_ms=5))
    noon_clear = next(p for p in clear if p.quantile == 0.5 and p.valid_time.hour == 12)
    noon_cloudy = next(p for p in cloudy if p.quantile == 0.5 and p.valid_time.hour == 12)
    assert noon_clear.value > noon_cloudy.value
    assert noon_clear.used_live_weather and noon_cloudy.used_live_weather


def test_real_wind_speed_drives_the_power_curve():
    asset = _FakeAsset("wind", rated_capacity_kw=30_000)
    calm = forecast_asset(asset, 6, 1.0, NOW, _flat_weather(cloud_cover_pct=0, wind_speed_ms=2))  # below cut-in
    strong = forecast_asset(asset, 6, 1.0, NOW, _flat_weather(cloud_cover_pct=0, wind_speed_ms=13))  # above rated
    calm_point = next(p for p in calm if p.quantile == 0.5)
    strong_point = next(p for p in strong if p.quantile == 0.5)
    assert calm_point.value == 0.0
    assert strong_point.value == pytest.approx(asset.rated_capacity_kw * 0.9)


def test_no_weather_falls_back_to_synthetic_model():
    asset = _FakeAsset("solar")
    points = forecast_asset(asset, 6, 1.0, NOW, weather=None)
    assert all(not p.used_live_weather for p in points)


def test_demand_and_price_are_unaffected_by_weather():
    weather = _flat_weather(cloud_cover_pct=100, wind_speed_ms=0)
    consumer = forecast_asset(_FakeAsset("consumer"), 6, 1.0, NOW, weather)
    grid = forecast_asset(_FakeAsset("grid_interconnection"), 6, 1.0, NOW, weather)
    assert all(not p.used_live_weather for p in consumer)
    assert all(not p.used_live_weather for p in grid)


class _FakeDB:
    def __init__(self):
        self.added = []

    def add_all(self, rows):
        self.added.extend(rows)

    def flush(self):
        pass


def test_generate_and_persist_forecasts_stamps_live_weather_model_version():
    db = _FakeDB()
    weather = _flat_weather(cloud_cover_pct=10, wind_speed_ms=9)
    generate_and_persist_forecasts(db, "tenant-1", [_FakeAsset("solar"), _FakeAsset("consumer", id="a2")], 3, 1.0, NOW, weather)
    solar_rows = [r for r in db.added if r.variable == "solar"]
    demand_rows = [r for r in db.added if r.variable == "demand"]
    assert solar_rows and all(r.model_version == LIVE_WEATHER_MODEL_VERSION for r in solar_rows)
    assert demand_rows and all(r.model_version == MODEL_VERSION for r in demand_rows)


def test_generate_and_persist_forecasts_without_weather_uses_baseline_version():
    db = _FakeDB()
    generate_and_persist_forecasts(db, "tenant-1", [_FakeAsset("solar")], 3, 1.0, NOW, weather=None)
    assert all(r.model_version == MODEL_VERSION for r in db.added)
