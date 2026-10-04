"""Portfolio registry: how a tenant describes its own estate — sites, assets
(solar, wind, battery, consumer, grid interconnection) and battery specs.

Without this the only way to get a portfolio into the platform was the demo
seed script. Everything here is tenant-scoped, validated against what the
optimizer actually requires, and audit-logged. Assets are retired (never
deleted) so the decision ledger and telemetry that reference them stay
intact."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.canonical import Asset, Battery, Portfolio, Site
from output.audit import append_audit_event

from ..deps import db_session, require_permission

router = APIRouter(prefix="/admin/portfolio", tags=["portfolio-registry"])

AssetKind = Literal["solar", "wind", "battery", "consumer", "grid_interconnection"]


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    grid_connection_point: str | None = None
    market_area: str = "GB"


class SiteOut(BaseModel):
    id: str
    name: str
    latitude: float | None
    longitude: float | None
    market_area: str


class BatterySpec(BaseModel):
    energy_capacity_kwh: float = Field(gt=0)
    power_limit_kw: float = Field(gt=0)
    soc_min_pct: float = Field(default=10.0, ge=0, le=100)
    soc_max_pct: float = Field(default=95.0, ge=0, le=100)
    soc_current_pct: float = Field(default=50.0, ge=0, le=100)
    soh_pct: float = Field(default=100.0, gt=0, le=100)
    round_trip_efficiency: float = Field(default=0.92, gt=0, le=1)
    degradation_cost_per_kwh_cycled: float = Field(default=0.02, ge=0)
    warranty_cycles_remaining: int | None = Field(default=None, ge=0)


class AssetIn(BaseModel):
    site_id: str
    name: str = Field(min_length=1, max_length=200)
    asset_type: AssetKind
    rated_capacity_kw: float = Field(gt=0)
    ramp_rate_kw_per_min: float | None = Field(default=None, gt=0)
    battery: BatterySpec | None = None  # required for (and only for) asset_type == "battery"


class AssetOut(BaseModel):
    id: str
    site_id: str
    site_name: str | None
    name: str
    asset_type: str
    rated_capacity_kw: float
    ramp_rate_kw_per_min: float | None
    retired: bool
    battery: BatterySpec | None


def _asset_out(asset: Asset, site_name: str | None, battery: Battery | None) -> AssetOut:
    return AssetOut(
        id=asset.id, site_id=asset.site_id, site_name=site_name, name=asset.name, asset_type=asset.asset_type,
        rated_capacity_kw=asset.rated_capacity_kw, ramp_rate_kw_per_min=asset.ramp_rate_kw_per_min,
        retired=asset.effective_to is not None,
        battery=BatterySpec(
            energy_capacity_kwh=battery.energy_capacity_kwh, power_limit_kw=battery.power_limit_kw,
            soc_min_pct=battery.soc_min_pct, soc_max_pct=battery.soc_max_pct, soc_current_pct=battery.soc_current_pct,
            soh_pct=battery.soh_pct, round_trip_efficiency=battery.round_trip_efficiency,
            degradation_cost_per_kwh_cycled=battery.degradation_cost_per_kwh_cycled,
            warranty_cycles_remaining=battery.warranty_cycles_remaining,
        ) if battery else None,
    )


@router.get("/sites", response_model=list[SiteOut])
def list_sites(ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)) -> list[SiteOut]:
    rows = db.execute(select(Site).order_by(Site.name)).scalars().all()
    return [SiteOut(id=s.id, name=s.name, latitude=s.latitude, longitude=s.longitude, market_area=s.market_area) for s in rows]


@router.post("/sites", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
def create_site(
    body: SiteIn, ctx: AuthContext = Depends(require_permission("manage:assets")), db: Session = Depends(db_session),
) -> SiteOut:
    portfolio = db.execute(select(Portfolio).where(Portfolio.tenant_id == ctx.tenant_id)).scalars().first()
    if portfolio is None:  # a tenant's first site creates its portfolio
        portfolio = Portfolio(tenant_id=ctx.tenant_id, name="Portfolio", description="Created with the first site")
        db.add(portfolio)
        db.flush()
    if db.execute(select(Site).where(Site.tenant_id == ctx.tenant_id, Site.name == body.name)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"a site named {body.name!r} already exists")
    site = Site(
        tenant_id=ctx.tenant_id, portfolio_id=portfolio.id, name=body.name, latitude=body.latitude,
        longitude=body.longitude, grid_connection_point=body.grid_connection_point, market_area=body.market_area,
    )
    db.add(site)
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="registry.site_created", payload={"site_id": site.id, "name": site.name})
    db.commit()
    return SiteOut(id=site.id, name=site.name, latitude=site.latitude, longitude=site.longitude, market_area=site.market_area)


@router.get("/assets", response_model=list[AssetOut])
def list_assets(ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)) -> list[AssetOut]:
    sites = {s.id: s.name for s in db.execute(select(Site)).scalars().all()}
    batteries = {b.asset_id: b for b in db.execute(select(Battery)).scalars().all()}
    assets = db.execute(select(Asset).order_by(Asset.asset_type, Asset.name)).scalars().all()
    return [_asset_out(a, sites.get(a.site_id), batteries.get(a.id)) for a in assets]


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def create_asset(
    body: AssetIn, ctx: AuthContext = Depends(require_permission("manage:assets")), db: Session = Depends(db_session),
) -> AssetOut:
    site = db.execute(select(Site).where(Site.id == body.site_id, Site.tenant_id == ctx.tenant_id)).scalar_one_or_none()
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site not found")
    if body.asset_type == "battery" and body.battery is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a battery asset needs its battery spec (energy_capacity_kwh, power_limit_kw, ...)")
    if body.asset_type != "battery" and body.battery is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only a battery asset takes a battery spec")
    if body.battery and body.battery.soc_min_pct >= body.battery.soc_max_pct:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "soc_min_pct must be below soc_max_pct")
    if body.battery and body.battery.power_limit_kw > body.rated_capacity_kw * 1.0001:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "battery power_limit_kw cannot exceed the asset's rated_capacity_kw")
    existing = db.execute(select(Asset).where(Asset.tenant_id == ctx.tenant_id, Asset.effective_to.is_(None))).scalars().all()
    if any(a.name == body.name for a in existing):
        raise HTTPException(status.HTTP_409_CONFLICT, f"an active asset named {body.name!r} already exists")
    if body.asset_type == "grid_interconnection" and any(a.asset_type == "grid_interconnection" for a in existing):
        # the optimizer plans against exactly one grid interconnection
        raise HTTPException(status.HTTP_409_CONFLICT, "this tenant already has a grid interconnection; retire it first to replace it")

    asset = Asset(
        tenant_id=ctx.tenant_id, site_id=site.id, name=body.name, asset_type=body.asset_type,
        rated_capacity_kw=body.rated_capacity_kw, ramp_rate_kw_per_min=body.ramp_rate_kw_per_min,
    )
    db.add(asset)
    db.flush()
    battery = None
    if body.battery:
        battery = Battery(tenant_id=ctx.tenant_id, asset_id=asset.id, **body.battery.model_dump())
        db.add(battery)
        db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="registry.asset_created",
                        payload={"asset_id": asset.id, "name": asset.name, "asset_type": asset.asset_type, "rated_capacity_kw": asset.rated_capacity_kw})
    db.commit()
    return _asset_out(asset, site.name, battery)


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    rated_capacity_kw: float | None = Field(default=None, gt=0)
    ramp_rate_kw_per_min: float | None = Field(default=None, gt=0)
    retired: bool | None = None  # true: take out of service (history is kept); false: bring it back


@router.patch("/assets/{asset_id}", response_model=AssetOut)
def update_asset(
    asset_id: str, body: AssetPatch, ctx: AuthContext = Depends(require_permission("manage:assets")), db: Session = Depends(db_session),
) -> AssetOut:
    asset = db.execute(select(Asset).where(Asset.id == asset_id, Asset.tenant_id == ctx.tenant_id)).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        asset.name = changes["name"]
    if "rated_capacity_kw" in changes:
        asset.rated_capacity_kw = changes["rated_capacity_kw"]
    if "ramp_rate_kw_per_min" in changes:
        asset.ramp_rate_kw_per_min = changes["ramp_rate_kw_per_min"]
    if "retired" in changes:
        asset.effective_to = datetime.now(timezone.utc) if changes["retired"] else None
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="registry.asset_updated", payload={"asset_id": asset.id, "changes": changes})
    db.commit()
    site = db.execute(select(Site).where(Site.id == asset.site_id)).scalar_one_or_none()
    battery = db.execute(select(Battery).where(Battery.asset_id == asset.id)).scalar_one_or_none()
    return _asset_out(asset, site.name if site else None, battery)
