"""Simulation Lab (PRD §9 workspace, doc 08 §4 demo scenarios): read/write
the shared scenario-control hash the edge-simulator polls every tick, so
cloud cover / wind surge / price spike / battery outage / line congestion /
demand shock can be driven from the dashboard without restarting anything.
"""

from __future__ import annotations

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from reo_common.config import get_settings
from reo_common.security import AuthContext

from ..deps import require_permission

router = APIRouter(prefix="/simulation", tags=["simulation"])
settings = get_settings()

SCENARIO_KEY = "reo:scenario:state"
_redis: redis.Redis | None = None


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


class ScenarioState(BaseModel):
    cloud_cover: float = Field(0.0, ge=0.0, le=1.0, description="fraction of solar output lost")
    wind_surge: float = Field(1.0, ge=0.0, le=3.0, description="multiplier on wind speed")
    price_spike: float = Field(1.0, ge=0.0, le=5.0, description="multiplier on market price")
    battery_outage_asset: str = Field("", description="asset id to force offline, or empty")
    line_congestion: bool = False
    demand_shock: float = Field(1.0, ge=0.0, le=5.0, description="multiplier on consumer demand")


@router.get("/scenario", response_model=ScenarioState)
def get_scenario(ctx: AuthContext = Depends(require_permission("manage:scenarios"))) -> ScenarioState:
    raw = _client().hgetall(SCENARIO_KEY)
    if not raw:
        return ScenarioState()
    return ScenarioState(
        cloud_cover=float(raw.get("cloud_cover", 0.0)),
        wind_surge=float(raw.get("wind_surge", 1.0)),
        price_spike=float(raw.get("price_spike", 1.0)),
        battery_outage_asset=raw.get("battery_outage_asset", ""),
        line_congestion=raw.get("line_congestion", "0") == "1",
        demand_shock=float(raw.get("demand_shock", 1.0)),
    )


@router.put("/scenario", response_model=ScenarioState)
def set_scenario(body: ScenarioState, ctx: AuthContext = Depends(require_permission("manage:scenarios"))) -> ScenarioState:
    _client().hset(SCENARIO_KEY, mapping={
        "cloud_cover": str(body.cloud_cover),
        "wind_surge": str(body.wind_surge),
        "price_spike": str(body.price_spike),
        "battery_outage_asset": body.battery_outage_asset,
        "line_congestion": "1" if body.line_congestion else "0",
        "demand_shock": str(body.demand_shock),
    })
    return body


@router.post("/scenario/reset", response_model=ScenarioState)
def reset_scenario(ctx: AuthContext = Depends(require_permission("manage:scenarios"))) -> ScenarioState:
    baseline = ScenarioState()
    return set_scenario(baseline, ctx)
