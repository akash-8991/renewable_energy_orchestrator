"""Synthetic portfolio telemetry generator ("edge" per BRD/TRD — a real
deployment runs this logic as AWS IoT Greengrass collectors on-site; here it
plays the same role against the seeded reference portfolio so the rest of
the platform has live data to work against without real hardware).

Publishes one CloudEvent per reading onto `reo.telemetry` (Redis Stream).
`api`'s ingestion consumer (`app/ingestion/telemetry_consumer.py`) is the
thing that validates and persists these — the simulator never writes to
Postgres directly, so it exercises the same ingestion path a real OPC
UA/MQTT feed would use.

Scenario control (doc 08 §4 demo steps 2-6): reads a small Redis hash
`reo:scenario:state` on every tick so the dashboard's future Scenario Lab
can drive cloud cover / wind surge / price spikes / battery outage / line
congestion / demand shocks without restarting this service.
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from database.connection import SessionLocal, break_glass_cross_tenant
from reo_common.events import CloudEvent, EventBus, STREAM_TELEMETRY
from models.canonical import Asset, Battery, Tenant
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s edge-simulator %(message)s")
log = logging.getLogger("edge-simulator")

TICK_SECONDS = 10
SCENARIO_KEY = "reo:scenario:state"

DEFAULT_SCENARIO = {
    "cloud_cover": "0.0",       # 0..1, fraction of solar output lost
    "wind_surge": "1.0",        # multiplier on wind speed
    "price_spike": "1.0",       # multiplier on market price
    "battery_outage_asset": "", # asset id to force offline, or ""
    "line_congestion": "0",     # "1" clamps grid export headroom hard
    "demand_shock": "1.0",      # multiplier on consumer demand
}


def _hour_fraction(now: datetime) -> float:
    return now.hour + now.minute / 60 + now.second / 3600


def solar_diurnal_factor(hour: float) -> float:
    """Simple daylight bell curve peaking at solar noon (12:00), zero before
    06:00 and after 20:00 — good enough to look and behave like a real solar
    profile without pulling in a full irradiance model."""
    if hour < 6 or hour > 20:
        return 0.0
    return max(0.0, math.sin(math.pi * (hour - 6) / 14)) ** 1.5


@dataclass
class WindState:
    speed_ms: float = 7.0

    def step(self, surge_multiplier: float) -> float:
        self.speed_ms += random.gauss(0, 0.6)
        self.speed_ms = max(0.0, min(28.0, self.speed_ms))
        return self.speed_ms * surge_multiplier


def wind_power_factor(speed_ms: float) -> float:
    """Simplified wind turbine power curve: cut-in 3 m/s, rated 12 m/s, cut-out 25 m/s."""
    if speed_ms < 3 or speed_ms > 25:
        return 0.0
    if speed_ms >= 12:
        return 1.0
    return ((speed_ms - 3) / 9) ** 3


def read_scenario_state(bus: EventBus) -> dict:
    raw = bus._redis.hgetall(SCENARIO_KEY)  # noqa: SLF001 — thin wrapper, no need for a new EventBus method yet
    if not raw:
        bus._redis.hset(SCENARIO_KEY, mapping=DEFAULT_SCENARIO)  # noqa: SLF001
        return dict(DEFAULT_SCENARIO)
    return {**DEFAULT_SCENARIO, **raw}


def publish(bus: EventBus, tenant_id: str, asset_id: str, metric: str, value: float, unit: str, quality: str = "good") -> None:
    event = CloudEvent(
        type="reo.telemetry.reading",
        source="edge-simulator",
        tenant_id=tenant_id,
        data={
            "asset_id": asset_id,
            "metric": metric,
            "event_time": datetime.now(timezone.utc).isoformat(),
            "value": round(value, 4),
            "unit": unit,
            "quality": quality,
            "source": "edge-simulator",
        },
    )
    bus.publish(STREAM_TELEMETRY, event)


def load_portfolio() -> tuple[str, list[Asset], dict[str, Battery]]:
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant)).scalars().first()
            if tenant is None:
                return "", [], {}
            assets = db.execute(select(Asset).where(Asset.tenant_id == tenant.id)).scalars().all()
            batteries = db.execute(select(Battery).where(Battery.tenant_id == tenant.id)).scalars().all()
            battery_by_asset = {b.asset_id: b for b in batteries}
            return tenant.id, list(assets), battery_by_asset
    finally:
        db.close()


def main() -> None:
    bus = EventBus()
    wind_states: dict[str, WindState] = {}
    battery_soc: dict[str, float] = {}

    tenant_id, assets, batteries = "", [], {}
    while not assets:
        tenant_id, assets, batteries = load_portfolio()
        if not assets:
            log.info("no seeded portfolio found yet — waiting for db/seed.py")
            time.sleep(5)

    for asset in assets:
        if asset.asset_type == "wind":
            wind_states[asset.id] = WindState(speed_ms=random.uniform(5, 9))
    for asset_id, battery in batteries.items():
        battery_soc[asset_id] = battery.soc_current_pct

    log.info("edge-simulator started for tenant %s, %d assets", tenant_id, len(assets))

    while True:
        scenario = read_scenario_state(bus)
        cloud_cover = float(scenario["cloud_cover"])
        wind_surge = float(scenario["wind_surge"])
        price_spike = float(scenario["price_spike"])
        outage_asset = scenario["battery_outage_asset"]
        line_congested = scenario["line_congestion"] == "1"
        demand_shock = float(scenario["demand_shock"])

        now = datetime.now(timezone.utc)
        hour = _hour_fraction(now)

        total_generation_kw = 0.0
        total_demand_kw = 0.0
        total_battery_net_kw = 0.0

        for asset in assets:
            if asset.asset_type == "solar":
                factor = solar_diurnal_factor(hour) * (1 - cloud_cover) * random.uniform(0.95, 1.0)
                power_kw = asset.rated_capacity_kw * factor
                irradiance = 1000 * factor  # W/m^2, illustrative
                publish(bus, tenant_id, asset.id, "power_kw", power_kw, "kW")
                publish(bus, tenant_id, asset.id, "irradiance_w_m2", irradiance, "W/m2")
                total_generation_kw += power_kw

            elif asset.asset_type == "wind":
                wind_state = wind_states[asset.id]
                speed = wind_state.step(wind_surge)
                factor = wind_power_factor(speed)
                power_kw = asset.rated_capacity_kw * factor
                publish(bus, tenant_id, asset.id, "power_kw", power_kw, "kW")
                publish(bus, tenant_id, asset.id, "wind_speed_ms", speed, "m/s")
                total_generation_kw += power_kw

            elif asset.asset_type == "consumer":
                # morning + evening peaks, per FR reference profile shape
                base = 0.5 + 0.3 * math.sin(math.pi * (hour - 7) / 12) ** 2
                demand_kw = asset.rated_capacity_kw * base * demand_shock * random.uniform(0.9, 1.05)
                publish(bus, tenant_id, asset.id, "power_kw", -abs(demand_kw), "kW")
                total_demand_kw += demand_kw

            elif asset.asset_type == "battery":
                battery = batteries.get(asset.id)
                if battery is None:
                    continue
                if asset.id == outage_asset:
                    publish(bus, tenant_id, asset.id, "power_kw", 0.0, "kW", quality="bad")
                    publish(bus, tenant_id, asset.id, "soc_pct", battery_soc[asset.id], "%", quality="stale")
                    continue
                # simple heuristic cycle: charge from midday solar surplus, discharge evening peak —
                # a real setpoint comes from the optimizer/command service once that phase lands
                if 10 <= hour < 15:
                    net_kw = min(asset.rated_capacity_kw, battery.power_limit_kw)
                elif 17 <= hour < 21:
                    net_kw = -min(asset.rated_capacity_kw, battery.power_limit_kw)
                else:
                    net_kw = 0.0
                capacity_kwh = battery.energy_capacity_kwh
                delta_pct = (net_kw * (TICK_SECONDS / 3600) / capacity_kwh) * 100 if capacity_kwh else 0
                new_soc = battery_soc[asset.id] + delta_pct
                new_soc = max(battery.soc_min_pct, min(battery.soc_max_pct, new_soc))
                battery_soc[asset.id] = new_soc
                publish(bus, tenant_id, asset.id, "power_kw", net_kw, "kW")
                publish(bus, tenant_id, asset.id, "soc_pct", new_soc, "%")
                publish(bus, tenant_id, asset.id, "temperature_c", 20 + random.uniform(-2, 6), "C")
                total_battery_net_kw += net_kw

            elif asset.asset_type == "grid_interconnection":
                base_price = 65.0 + 20 * math.sin(math.pi * (hour - 6) / 12)
                price = max(5.0, base_price * price_spike + random.uniform(-3, 3))
                publish(bus, tenant_id, asset.id, "market_price_gbp_per_mwh", price, "GBP/MWh")
                publish(bus, tenant_id, asset.id, "frequency_hz", 50.0 + random.gauss(0, 0.02), "Hz")

        net_import_kw = total_demand_kw - total_generation_kw + total_battery_net_kw
        grid_asset = next((a for a in assets if a.asset_type == "grid_interconnection"), None)
        if grid_asset is not None:
            if line_congested:
                net_import_kw = max(-grid_asset.rated_capacity_kw * 0.3, min(grid_asset.rated_capacity_kw * 0.3, net_import_kw))
            publish(bus, tenant_id, grid_asset.id, "net_import_kw", net_import_kw, "kW")

        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
