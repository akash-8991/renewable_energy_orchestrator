from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from reo_common.models import Asset, Battery, Portfolio, Site, Telemetry
from reo_common.security import AuthContext
from reo_common.twin import assess_freshness, latest_readings_for_tenant
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/twin", tags=["twin"])


class AssetSnapshot(BaseModel):
    id: str
    name: str
    asset_type: str
    site_id: str
    rated_capacity_kw: float
    latest: dict[str, dict] = {}


class SiteSnapshot(BaseModel):
    id: str
    name: str
    assets: list[AssetSnapshot]


class PortfolioSnapshot(BaseModel):
    id: str
    name: str
    sites: list[SiteSnapshot]


@router.get("/portfolio", response_model=list[PortfolioSnapshot])
def get_portfolio_snapshot(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)
) -> list[PortfolioSnapshot]:
    portfolios = db.execute(select(Portfolio)).scalars().all()
    readings = latest_readings_for_tenant(db, ctx.tenant_id)
    by_asset: dict[str, dict] = {}
    for r in readings:
        by_asset.setdefault(r.asset_id, {})[r.metric] = {
            "value": r.value, "unit": r.unit, "event_time": r.event_time.isoformat(),
            "quality": r.quality, "freshness": r.freshness, "confidence": r.confidence,
            "age_seconds": round(r.age_seconds, 1),
        }

    out = []
    for portfolio in portfolios:
        sites = db.execute(select(Site).where(Site.portfolio_id == portfolio.id)).scalars().all()
        site_snapshots = []
        for site in sites:
            assets = db.execute(select(Asset).where(Asset.site_id == site.id)).scalars().all()
            asset_snapshots = [
                AssetSnapshot(
                    id=a.id, name=a.name, asset_type=a.asset_type, site_id=site.id,
                    rated_capacity_kw=a.rated_capacity_kw, latest=by_asset.get(a.id, {}),
                )
                for a in assets
            ]
            site_snapshots.append(SiteSnapshot(id=site.id, name=site.name, assets=asset_snapshots))
        out.append(PortfolioSnapshot(id=portfolio.id, name=portfolio.name, sites=site_snapshots))
    return out


class BatterySnapshot(BaseModel):
    asset_id: str
    soc_pct: float
    soh_pct: float
    energy_capacity_kwh: float
    power_limit_kw: float
    soc_min_pct: float
    soc_max_pct: float


@router.get("/batteries", response_model=list[BatterySnapshot])
def get_batteries(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)
) -> list[BatterySnapshot]:
    batteries = db.execute(select(Battery)).scalars().all()
    # soc_pct comes from live telemetry, not the seeded/static Battery row —
    # the row only holds slow-changing nameplate data (capacity, limits, SoH)
    readings = latest_readings_for_tenant(db, ctx.tenant_id, asset_ids=[b.asset_id for b in batteries])
    latest_soc = {r.asset_id: r.value for r in readings if r.metric == "soc_pct"}
    return [
        BatterySnapshot(
            asset_id=b.asset_id, soc_pct=latest_soc.get(b.asset_id, b.soc_current_pct), soh_pct=b.soh_pct,
            energy_capacity_kwh=b.energy_capacity_kwh, power_limit_kw=b.power_limit_kw,
            soc_min_pct=b.soc_min_pct, soc_max_pct=b.soc_max_pct,
        )
        for b in batteries
    ]


class TelemetryPoint(BaseModel):
    event_time: str
    value: float
    quality: str


@router.get("/assets/{asset_id}/telemetry", response_model=list[TelemetryPoint])
def get_asset_telemetry(
    asset_id: str,
    metric: str = Query(...),
    minutes: int = Query(60, le=7 * 24 * 60),
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> list[TelemetryPoint]:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = db.execute(
        select(Telemetry)
        .where(Telemetry.asset_id == asset_id, Telemetry.metric == metric, Telemetry.event_time >= since)
        .order_by(Telemetry.event_time.asc())
    ).scalars().all()
    return [TelemetryPoint(event_time=r.event_time.isoformat(), value=r.value, quality=r.quality) for r in rows]
