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
  - 01_customer_demographics.csv and 02_customer_energy_consumption_tariff.csv
    (individual per-customer billing data for 100 residential/SME/industrial
    customers) ARE ingested — by ingest_customers() below — into the
    Customer/CustomerReading tables added specifically for this (models/
    canonical.py), since Asset/Battery model the utility's own portfolio of
    generation/storage/demand *assets*, not 100 separately metered retail
    accounts, and force-fitting them onto Asset would misrepresent what
    those rows are. The 15-minute readings file is pre-aggregated to one
    row per customer per day on ingestion (863k rows -> ~9k) — see
    CustomerReading's docstring for why daily granularity is the right
    grain for customer-level insights rather than a live control-loop.
  - 05_battery.csv (per-customer home/business battery specs) is still NOT
    ingested — Customer already carries battery_installed/battery_capacity_
    kwh from the demographics file, which is what the Customer Insights UI
    needs; 05_battery's per-cycle degradation detail has no consumer yet.
  - Timestamps are shifted so the most recent row in the requested window
    lands at "now" (preserving each row's original relative spacing) —
    this is real historical data replayed to look live, not fabricated
    values, and is stated as such wherever these readings surface
    (`source` field on every reading is tagged `hackathon_dataset:<file>`).
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reo_common.config import get_settings
from reo_common.events import EventBus
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from database.connection import SessionLocal, break_glass_cross_tenant
from models.canonical import Asset, Customer, CustomerReading, Tenant

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


def _to_float(v: str | None) -> float | None:
    return float(v) if v not in (None, "") else None


def ingest_customers() -> dict[str, int]:
    """Ingests the reference dataset's two retail-customer files into
    Customer (one row per source customer, from 01_customer_demographics)
    and CustomerReading (one row per customer per day, aggregated on the
    way in from 02_customer_energy_consumption_tariff's 863k 15-minute
    rows). Idempotent — ON CONFLICT DO NOTHING on each table's unique
    constraint, so re-running this after a partial run or on container
    restart only fills in what's missing."""
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug)).scalar_one_or_none()
        if tenant is None:
            raise RuntimeError(f"no tenant with slug={settings.default_tenant_slug!r} — run database/seed.py first")
        tenant_id = tenant.id

        demo_rows = _read_csv_rows("01_customer_demographics.csv", limit_last_n=0)
        for row in demo_rows:
            stmt = pg_insert(Customer).values(
                tenant_id=tenant_id, customer_ref=row["customer_id"],
                customer_type=row["customer_type"], region=row["region"],
                annual_consumption_kwh=float(row["annual_consumption_kwh"] or 0),
                renewable_profile=row["renewable_profile"] or None,
                solar_capacity_kw=float(row["solar_capacity_kw"] or 0),
                wind_capacity_kw=float(row["wind_capacity_kw"] or 0),
                battery_installed=row["battery_installed"] == "1",
                battery_capacity_kwh=float(row["battery_capacity_kwh"] or 0),
                tariff_plan=row["tariff_plan"] or None,
                standing_charge_gbp_day=_to_float(row["standing_charge_gbp_day"]),
                base_rate_gbp_kwh=_to_float(row["base_rate_gbp_kwh"]),
                offpeak_rate_gbp_kwh=_to_float(row["offpeak_rate_gbp_kwh"]),
                occupants=_to_float(row["occupants"]),
                property_size_m2=_to_float(row["property_size_m2"]),
                business_size=row["business_size"] or None,
            ).on_conflict_do_nothing(constraint="uq_customer_ref")
            db.execute(stmt)
        db.commit()

        # Re-select rather than trusting the inserts above: ON CONFLICT DO
        # NOTHING silently skips rows that already existed from a prior run,
        # so this is the only reliable way to get every customer_ref -> id
        # mapping, not just the ones inserted just now. Needs break_glass:
        # this script runs with no tenant context set (it's not a request),
        # and the tenant-isolation ORM listener (database/connection.py)
        # fails a SELECT closed to zero rows rather than open when no
        # context is set — the same reason _load_tenant_assets() above
        # wraps its own Asset select the same way.
        with break_glass_cross_tenant():
            customer_id_by_ref = {
                ref: cid
                for cid, ref in db.execute(select(Customer.id, Customer.customer_ref).where(Customer.tenant_id == tenant_id)).all()
            }

        reading_rows = _read_csv_rows("02_customer_energy_consumption_tariff.csv", limit_last_n=0)
        daily: dict[tuple[str, str], dict[str, float]] = {}
        for row in reading_rows:
            ref = row["customer_id"]
            day = row["timestamp"][:10]  # YYYY-MM-DD — good enough for a daily bucket key
            bucket = daily.setdefault((ref, day), {
                "consumption_kwh": 0.0, "solar_generation_kwh": 0.0, "wind_generation_kwh": 0.0,
                "net_grid_import_kwh": 0.0, "export_kwh": 0.0, "energy_cost_gbp": 0.0, "standing_charge_gbp": 0.0,
            })
            consumption = float(row["consumption_kwh"] or 0)
            rate = float(row["energy_rate_gbp_kwh"] or 0)
            bucket["consumption_kwh"] += consumption
            bucket["solar_generation_kwh"] += float(row["solar_generation_kwh"] or 0)
            bucket["wind_generation_kwh"] += float(row["wind_generation_kwh"] or 0)
            bucket["net_grid_import_kwh"] += float(row["net_grid_import_kwh"] or 0)
            bucket["export_kwh"] += float(row["export_kwh"] or 0)
            bucket["energy_cost_gbp"] += consumption * rate
            bucket["standing_charge_gbp"] += float(row["standing_charge_gbp_15min"] or 0)

        insert_rows = []
        skipped_unknown_customer = 0
        for (ref, day), agg in daily.items():
            cid = customer_id_by_ref.get(ref)
            if cid is None:
                skipped_unknown_customer += 1
                continue  # a reading for a customer_id absent from demographics — don't fabricate a profile for it
            insert_rows.append({
                "tenant_id": tenant_id, "customer_id": cid,
                "event_time": datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
                "consumption_kwh": agg["consumption_kwh"], "solar_generation_kwh": agg["solar_generation_kwh"],
                "wind_generation_kwh": agg["wind_generation_kwh"], "net_grid_import_kwh": agg["net_grid_import_kwh"],
                "export_kwh": agg["export_kwh"],
                "avg_energy_rate_gbp_kwh": (agg["energy_cost_gbp"] / agg["consumption_kwh"]) if agg["consumption_kwh"] else 0.0,
                "estimated_cost_gbp": agg["energy_cost_gbp"] + agg["standing_charge_gbp"],
            })
        if skipped_unknown_customer:
            log.warning("skipped %d daily readings for customer_ids not present in demographics", skipped_unknown_customer)

        BATCH = 1000
        for i in range(0, len(insert_rows), BATCH):
            chunk = insert_rows[i:i + BATCH]
            stmt = pg_insert(CustomerReading).on_conflict_do_nothing(constraint="uq_customer_reading")
            db.execute(stmt, chunk)
        db.commit()

        return {"customers": len(customer_id_by_ref), "customer_reading_days": len(insert_rows)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Connector-driven ingestion — same reference-dataset mapping as above, but
# called from a live authenticated request (backend/app/routers/connectors.py)
# instead of the standalone script's own SessionLocal()/break_glass lifecycle,
# and keyed to a single already-loaded file/table's rows rather than a fixed
# lookback window. Two connector kinds feed this: a `data_table` connector
# whose path resolves to one of these filenames, and a `database` connector
# whose table_name matches one of these keys — either way, by the time rows
# reach ingest_reference_rows() they're just plain dicts (str values from a
# CSV, native types from a DB row); every field access below tolerates both.
#
# Deliberately NOT sharing code with ingest_portfolio_series()/
# ingest_customers() above: those are already-verified, hours-windowed,
# break_glass-based logic for the manual script, and duplicating their small
# per-file mappings here (this time un-windowed, request-scoped, no
# break_glass) is less risky than threading two very different call contexts
# through one shared implementation.
# ---------------------------------------------------------------------------

KNOWN_TABLE_KEYS = {
    "customer_demographics",
    "customer_energy_consumption_tariff",
    "renewable_generation",
    "grid",
    "market",
    "external_weather",
    "battery",
    "scenario_actions",
}

# Present in the reference dataset but not (yet) mapped onto a canonical
# entity — same documented gap as ingest_portfolio_series()/ingest_customers()
# above, just also enforced for the connector paths instead of silently
# ingesting nothing.
UNMAPPED_TABLE_KEYS = {"battery", "scenario_actions"}


def normalize_table_key(name: str) -> str:
    """"03_renewable_generation.csv" (a data_table connector's local
    filename) and "renewable_generation" (a database connector's table
    name) both normalize to "renewable_generation" — one recognizer for
    both connector kinds."""
    stem = Path(name).stem
    stem = re.sub(r"^\d+[_-]", "", stem)
    return stem.strip().lower()


def _by_type_and_grid(db: Session, tenant_id: str) -> tuple[dict[str, list[Asset]], Asset | None]:
    assets = db.execute(select(Asset).where(Asset.tenant_id == tenant_id)).scalars().all()
    by_type: dict[str, list[Asset]] = {}
    grid = None
    for a in assets:
        by_type.setdefault(a.asset_type, []).append(a)
        if a.asset_type == "grid_interconnection":
            grid = a
    return by_type, grid


def _row_ts(row: dict, key: str = "timestamp") -> str:
    value = row[key]
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _upsert_customer_demographics_rows(db: Session, tenant_id: str, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        stmt = pg_insert(Customer).values(
            tenant_id=tenant_id, customer_ref=row["customer_id"],
            customer_type=row["customer_type"], region=row["region"],
            annual_consumption_kwh=float(row["annual_consumption_kwh"] or 0),
            renewable_profile=row["renewable_profile"] or None,
            solar_capacity_kw=float(row["solar_capacity_kw"] or 0),
            wind_capacity_kw=float(row["wind_capacity_kw"] or 0),
            battery_installed=str(row["battery_installed"]) in ("1", "True", "true"),
            battery_capacity_kwh=float(row["battery_capacity_kwh"] or 0),
            tariff_plan=row["tariff_plan"] or None,
            standing_charge_gbp_day=_to_float(row["standing_charge_gbp_day"]),
            base_rate_gbp_kwh=_to_float(row["base_rate_gbp_kwh"]),
            offpeak_rate_gbp_kwh=_to_float(row["offpeak_rate_gbp_kwh"]),
            occupants=_to_float(row["occupants"]),
            property_size_m2=_to_float(row["property_size_m2"]),
            business_size=row["business_size"] or None,
        ).on_conflict_do_nothing(constraint="uq_customer_ref")
        result = db.execute(stmt)
        count += result.rowcount or 0
    db.flush()
    return count


def _upsert_customer_reading_rows(db: Session, tenant_id: str, rows: list[dict]) -> tuple[int, int]:
    customer_id_by_ref = {
        ref: cid
        for cid, ref in db.execute(
            select(Customer.id, Customer.customer_ref).where(Customer.tenant_id == tenant_id)
        ).all()
    }

    daily: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        ref = row["customer_id"]
        day = _row_ts(row)[:10]
        bucket = daily.setdefault((ref, day), {
            "consumption_kwh": 0.0, "solar_generation_kwh": 0.0, "wind_generation_kwh": 0.0,
            "net_grid_import_kwh": 0.0, "export_kwh": 0.0, "energy_cost_gbp": 0.0, "standing_charge_gbp": 0.0,
        })
        consumption = float(row["consumption_kwh"] or 0)
        rate = float(row["energy_rate_gbp_kwh"] or 0)
        bucket["consumption_kwh"] += consumption
        bucket["solar_generation_kwh"] += float(row["solar_generation_kwh"] or 0)
        bucket["wind_generation_kwh"] += float(row["wind_generation_kwh"] or 0)
        bucket["net_grid_import_kwh"] += float(row["net_grid_import_kwh"] or 0)
        bucket["export_kwh"] += float(row["export_kwh"] or 0)
        bucket["energy_cost_gbp"] += consumption * rate
        bucket["standing_charge_gbp"] += float(row["standing_charge_gbp_15min"] or 0)

    insert_rows = []
    skipped_unknown_customer = 0
    for (ref, day), agg in daily.items():
        cid = customer_id_by_ref.get(ref)
        if cid is None:
            skipped_unknown_customer += 1
            continue
        insert_rows.append({
            "tenant_id": tenant_id, "customer_id": cid,
            "event_time": datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
            "consumption_kwh": agg["consumption_kwh"], "solar_generation_kwh": agg["solar_generation_kwh"],
            "wind_generation_kwh": agg["wind_generation_kwh"], "net_grid_import_kwh": agg["net_grid_import_kwh"],
            "export_kwh": agg["export_kwh"],
            "avg_energy_rate_gbp_kwh": (agg["energy_cost_gbp"] / agg["consumption_kwh"]) if agg["consumption_kwh"] else 0.0,
            "estimated_cost_gbp": agg["energy_cost_gbp"] + agg["standing_charge_gbp"],
        })

    BATCH = 1000
    for i in range(0, len(insert_rows), BATCH):
        chunk = insert_rows[i:i + BATCH]
        stmt = pg_insert(CustomerReading).on_conflict_do_nothing(constraint="uq_customer_reading")
        db.execute(stmt, chunk)
    db.flush()
    return len(insert_rows), skipped_unknown_customer


def ingest_reference_rows(db: Session, tenant_id: str, table_key: str, rows: list[dict], *, source_label: str) -> dict:
    """Routes already-loaded rows for one of the reference dataset's known
    tables through the platform's canonical mapping — real Asset telemetry
    for the four portfolio-level series, real Customer/CustomerReading rows
    for the two retail-customer files. Always returns a summary dict with at
    least {"table", "status"} rather than raising, so a connector's ingest
    endpoint can report a clear per-table outcome instead of a 500."""
    if table_key not in KNOWN_TABLE_KEYS:
        return {"table": table_key, "status": "unknown", "message": f"{table_key!r} isn't a recognized reference-dataset table"}
    if table_key in UNMAPPED_TABLE_KEYS:
        return {"table": table_key, "status": "not_mapped",
                "message": f"{table_key!r} isn't mapped onto a canonical entity yet (see hackathon_dataset.py's documented scope)"}
    if not rows:
        return {"table": table_key, "status": "empty", "count": 0}

    bus = EventBus()

    if table_key == "renewable_generation":
        by_type, _grid = _by_type_and_grid(db, tenant_id)
        solar_assets, wind_assets = by_type.get("solar", []), by_type.get("wind", [])
        if not solar_assets or not wind_assets:
            return {"table": table_key, "status": "error", "message": "tenant has no solar/wind assets — run database/seed.py first"}
        timestamps = _shifted_timestamps(len(rows))
        readings = []
        for row, ts in zip(rows, timestamps):
            solar_kw = float(row["solar_output_mw"]) * 1000.0
            wind_kw = float(row["wind_output_mw"]) * 1000.0
            for asset, share_kw in _split_by_capacity(solar_kw, solar_assets):
                readings.append({"asset_id": asset.id, "metric": "power_kw", "event_time": ts, "value": share_kw,
                                  "unit": "kW", "quality": "good", "source": source_label})
            for asset, share_kw in _split_by_capacity(wind_kw, wind_assets):
                readings.append({"asset_id": asset.id, "metric": "power_kw", "event_time": ts, "value": share_kw,
                                  "unit": "kW", "quality": "good", "source": source_label})
        lineage_id = publish_readings(bus, tenant_id, readings)
        return {"table": table_key, "status": "ingested", "count": len(readings), "lineage_id": lineage_id, "kind": "telemetry"}

    if table_key == "grid":
        _by_type, grid = _by_type_and_grid(db, tenant_id)
        if grid is None:
            return {"table": table_key, "status": "error", "message": "tenant has no grid_interconnection asset"}
        timestamps = _shifted_timestamps(len(rows))
        readings = [
            {"asset_id": grid.id, "metric": "frequency_hz", "event_time": ts, "value": float(row["grid_frequency_hz"]),
             "unit": "Hz", "quality": "good", "source": source_label}
            for row, ts in zip(rows, timestamps)
        ]
        lineage_id = publish_readings(bus, tenant_id, readings)
        return {"table": table_key, "status": "ingested", "count": len(readings), "lineage_id": lineage_id, "kind": "telemetry"}

    if table_key == "market":
        _by_type, grid = _by_type_and_grid(db, tenant_id)
        if grid is None:
            return {"table": table_key, "status": "error", "message": "tenant has no grid_interconnection asset"}
        timestamps = _shifted_timestamps(len(rows))
        readings = [
            {"asset_id": grid.id, "metric": "market_price_gbp_per_mwh", "event_time": ts,
             "value": float(row["electricity_price_gbp_mwh"]), "unit": "GBP/MWh", "quality": "good", "source": source_label}
            for row, ts in zip(rows, timestamps)
        ]
        lineage_id = publish_readings(bus, tenant_id, readings)
        return {"table": table_key, "status": "ingested", "count": len(readings), "lineage_id": lineage_id, "kind": "telemetry"}

    if table_key == "external_weather":
        by_type, _grid = _by_type_and_grid(db, tenant_id)
        solar_assets, wind_assets = by_type.get("solar", []), by_type.get("wind", [])
        timestamps = _shifted_timestamps(len(rows))
        readings = []
        for row, ts in zip(rows, timestamps):
            temp = float(row["temperature_c"])
            wind_speed = float(row["wind_speed_mps"])
            for asset in solar_assets:
                readings.append({"asset_id": asset.id, "metric": "temperature_c", "event_time": ts, "value": temp,
                                  "unit": "C", "quality": "good", "source": source_label})
            for asset in wind_assets:
                readings.append({"asset_id": asset.id, "metric": "wind_speed_ms", "event_time": ts, "value": wind_speed,
                                  "unit": "m/s", "quality": "good", "source": source_label})
        lineage_id = publish_readings(bus, tenant_id, readings)
        return {"table": table_key, "status": "ingested", "count": len(readings), "lineage_id": lineage_id, "kind": "telemetry"}

    if table_key == "customer_demographics":
        count = _upsert_customer_demographics_rows(db, tenant_id, rows)
        return {"table": table_key, "status": "ingested", "count": count, "kind": "customers"}

    if table_key == "customer_energy_consumption_tariff":
        count, skipped = _upsert_customer_reading_rows(db, tenant_id, rows)
        return {"table": table_key, "status": "ingested", "count": count, "skipped_unknown_customer": skipped, "kind": "customer_readings"}

    raise AssertionError(f"unhandled known table_key {table_key!r}")  # pragma: no cover — KNOWN_TABLE_KEYS/UNMAPPED_TABLE_KEYS above are exhaustive for every branch here


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s hackathon-dataset %(message)s")
    counts = ingest_portfolio_series(hours=8)
    total = sum(counts.values())
    log.info("ingested %d readings from the reference dataset: %s", total, counts)
    customer_counts = ingest_customers()
    log.info("ingested retail customer data: %s", customer_counts)


if __name__ == "__main__":
    main()
