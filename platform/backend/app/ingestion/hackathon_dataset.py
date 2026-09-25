"""Ingests the real reference dataset dropped in `/data`
(`Renewable_Energy_Orchestrator_Dataset` — 100 UK customers, 15-minute
resolution, 2026-01-01 to 2026-03-31, seed 42) into the platform's live
telemetry pipeline, instead of leaving it sitting in the watched folder as
files the generic long-format parser (`file_ingest.py`) can't shape-match.

Scope, stated honestly:
  - The four portfolio-level series (03_renewable_generation, 04_grid,
    06_market, 07_external_weather) map cleanly onto the platform's
    existing canonical metrics (`_KNOWN_METRICS` in telemetry_consumer.py)
    with zero schema changes, attributed to the seeded reference
    portfolio's real assets (solar/wind output split proportionally by
    each asset's rated capacity share; price/frequency on the grid
    interconnection; temperature/wind-speed broadcast to the relevant
    generation assets). This is what `ingest_portfolio_series()` below
    does, and it's real: the readings go through the identical
    `reo.telemetry` Redis stream + `_validate()` + persistence path any
    other telemetry does.
  - The three customer-level files (01_customer_demographics,
    02_customer_energy_consumption_tariff, 05_battery — individual
    per-customer billing/battery data for 100 residential/SME/industrial
    customers) are NOT ingested here. The platform's canonical model has
    no "individual metered customer" entity — Asset/Battery model a
    portfolio's generation/storage/demand *assets*, not 100 separate
    domestic meters — force-fitting them onto the existing Asset table
    would misrepresent what those rows are. Genuinely supporting this
    dimension would need a new canonical entity (something like
    `Customer`/`Meter`) and is out of scope of what a same-session
    ingestion pass can respectably add; see docs/SIMPLIFICATIONS.md.
  - Timestamps are shifted so the most recent row in the requested window
    lands at "now" (preserving each row's original relative spacing) —
    this is real historical data replayed to look live, not fabricated
    values, and is stated as such wherever these readings surface
    (`source` field on every reading is tagged `hackathon_dataset:<file>`).
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database.connection import SessionLocal, break_glass_cross_tenant
from models.canonical import Asset, Tenant
from reo_common.config import get_settings
from reo_common.events import EventBus
from sqlalchemy import select

from .file_ingest import publish_readings

log = logging.getLogger("api.ingestion.hackathon_dataset")
settings = get_settings()

DATA_DIR = Path(settings.data_watch_dir)
STEP_MINUTES = 15


def _load_tenant_assets(db) -> tuple[str, dict[str, list[Asset]], Asset | None]:
    with break_glass_cross_tenant():
        tenant = db.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug)).scalar_one_or_none()
        if tenant is None:
            raise RuntimeError(f"no tenant with slug={settings.default_tenant_slug!r} — run database/seed.py first")
        assets = db.execute(select(Asset).where(Asset.tenant_id == tenant.id)).scalars().all()
    by_type: dict[str, list[Asset]] = {}
    grid = None
    for a in assets:
        by_type.setdefault(a.asset_type, []).append(a)
        if a.asset_type == "grid_interconnection":
            grid = a
    return tenant.id, by_type, grid


def _read_csv_rows(filename: str, limit_last_n: int) -> list[dict]:
    path = DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"expected reference dataset file at {path} — is /data mounted?")
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit_last_n:] if limit_last_n else rows


def _shifted_timestamps(n: int, *, step_minutes: int = STEP_MINUTES) -> list[str]:
    """Row i (0-indexed, oldest first) lands at now - (n-1-i)*step — the
    last row is "now", earlier rows step backwards, preserving spacing."""
    now = datetime.now(timezone.utc)
    return [(now - timedelta(minutes=step_minutes * (n - 1 - i))).isoformat() for i in range(n)]


def _split_by_capacity(total_value: float, assets: list[Asset]) -> list[tuple[Asset, float]]:
    total_capacity = sum(a.rated_capacity_kw for a in assets) or 1.0
    return [(a, total_value * (a.rated_capacity_kw / total_capacity)) for a in assets]


def ingest_portfolio_series(*, hours: int = 8) -> dict[str, int]:
    """Ingests the last `hours` of the four portfolio-level dataset files as
    real telemetry readings for the seeded reference tenant's actual
    assets. Returns a dict of {source_file: reading_count} for reporting."""
    n = int(hours * 60 / STEP_MINUTES)
    db = SessionLocal()
    try:
        tenant_id, by_type, grid = _load_tenant_assets(db)
    finally:
        db.close()

    solar_assets = by_type.get("solar", [])
    wind_assets = by_type.get("wind", [])
    if not solar_assets or not wind_assets or grid is None:
        raise RuntimeError("seeded tenant is missing solar/wind/grid_interconnection assets — run database/seed.py first")

    bus = EventBus()
    counts: dict[str, int] = {}

    # 03_renewable_generation.csv: solar_output_mw / wind_output_mw (portfolio
    # aggregate) -> power_kw per asset, split proportionally by rated capacity.
    rows = _read_csv_rows("03_renewable_generation.csv", n)
    timestamps = _shifted_timestamps(len(rows))
    readings = []
    for row, ts in zip(rows, timestamps):
        solar_kw = float(row["solar_output_mw"]) * 1000.0
        wind_kw = float(row["wind_output_mw"]) * 1000.0
        for asset, share_kw in _split_by_capacity(solar_kw, solar_assets):
            readings.append({"asset_id": asset.id, "metric": "power_kw", "event_time": ts, "value": share_kw,
                              "unit": "kW", "quality": "good", "source": "hackathon_dataset:03_renewable_generation.csv"})
        for asset, share_kw in _split_by_capacity(wind_kw, wind_assets):
            readings.append({"asset_id": asset.id, "metric": "power_kw", "event_time": ts, "value": share_kw,
                              "unit": "kW", "quality": "good", "source": "hackathon_dataset:03_renewable_generation.csv"})
    publish_readings(bus, tenant_id, readings)
    counts["03_renewable_generation.csv"] = len(readings)

    # 04_grid.csv: grid_frequency_hz -> frequency_hz on the grid interconnection.
    rows = _read_csv_rows("04_grid.csv", n)
    timestamps = _shifted_timestamps(len(rows))
    readings = [
        {"asset_id": grid.id, "metric": "frequency_hz", "event_time": ts, "value": float(row["grid_frequency_hz"]),
         "unit": "Hz", "quality": "good", "source": "hackathon_dataset:04_grid.csv"}
        for row, ts in zip(rows, timestamps)
    ]
    publish_readings(bus, tenant_id, readings)
    counts["04_grid.csv"] = len(readings)

    # 06_market.csv: electricity_price_gbp_mwh -> market_price_gbp_per_mwh on the grid interconnection.
    rows = _read_csv_rows("06_market.csv", n)
    timestamps = _shifted_timestamps(len(rows))
    readings = [
        {"asset_id": grid.id, "metric": "market_price_gbp_per_mwh", "event_time": ts,
         "value": float(row["electricity_price_gbp_mwh"]), "unit": "GBP/MWh", "quality": "good",
         "source": "hackathon_dataset:06_market.csv"}
        for row, ts in zip(rows, timestamps)
    ]
    publish_readings(bus, tenant_id, readings)
    counts["06_market.csv"] = len(readings)

    # 07_external_weather.csv: temperature_c -> solar assets; wind_speed_mps -> wind assets (m/s either way).
    rows = _read_csv_rows("07_external_weather.csv", n)
    timestamps = _shifted_timestamps(len(rows))
    readings = []
    for row, ts in zip(rows, timestamps):
        temp = float(row["temperature_c"])
        wind_speed = float(row["wind_speed_mps"])
        for asset in solar_assets:
            readings.append({"asset_id": asset.id, "metric": "temperature_c", "event_time": ts, "value": temp,
                              "unit": "C", "quality": "good", "source": "hackathon_dataset:07_external_weather.csv"})
        for asset in wind_assets:
            readings.append({"asset_id": asset.id, "metric": "wind_speed_ms", "event_time": ts, "value": wind_speed,
                              "unit": "m/s", "quality": "good", "source": "hackathon_dataset:07_external_weather.csv"})
    publish_readings(bus, tenant_id, readings)
    counts["07_external_weather.csv"] = len(readings)

    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s hackathon-dataset %(message)s")
    counts = ingest_portfolio_series(hours=8)
    total = sum(counts.values())
    log.info("ingested %d readings from the reference dataset: %s", total, counts)


if __name__ == "__main__":
    main()
