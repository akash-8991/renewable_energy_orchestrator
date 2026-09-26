"""Configuration Studio (production-readiness punch list, items closed as
runtime-configurable settings rather than env-var-only, redeploy-required
knobs): the model gateway's circuit breaker/timeout budget, the live
weather feed that replaces `policy/forecast.py`'s synthetic solar/wind
curves, and whether SSO login is enabled for this tenant. `GET` is visible
to anyone who can see the dashboard; `PUT` requires `manage:settings`
(TENANT_ADMIN) or `manage:platform_config` (PLATFORM_ADMIN, a superuser
that already holds every permission — see `reo_common.security`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.security import AuthContext
from sqlalchemy.orm import Session

from models.canonical import PlatformSettings
from output.audit import append_audit_event
from reo_common.platform_settings import get_or_create_platform_settings

from ..deps import db_session, require_permission

router = APIRouter(prefix="/configuration", tags=["configuration"])


class PlatformSettingsView(BaseModel):
    live_weather_enabled: bool
    weather_site_lat: float | None
    weather_site_lon: float | None
    gateway_circuit_breaker_enabled: bool
    gateway_timeout_seconds: float
    gateway_failure_threshold: int
    gateway_cooldown_seconds: int
    sso_enabled: bool
    sso_available: bool  # true only when this deployment actually has an OIDC issuer configured
    updated_by: str | None
    updated_at: str


def _to_view(row: PlatformSettings) -> PlatformSettingsView:
    from reo_common.config import get_settings

    return PlatformSettingsView(
        live_weather_enabled=row.live_weather_enabled,
        weather_site_lat=row.weather_site_lat,
        weather_site_lon=row.weather_site_lon,
        gateway_circuit_breaker_enabled=row.gateway_circuit_breaker_enabled,
        gateway_timeout_seconds=row.gateway_timeout_seconds,
        gateway_failure_threshold=row.gateway_failure_threshold,
        gateway_cooldown_seconds=row.gateway_cooldown_seconds,
        sso_enabled=row.sso_enabled,
        sso_available=bool(get_settings().oidc_issuer),
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat(),
    )


@router.get("/settings", response_model=PlatformSettingsView)
def get_settings_endpoint(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session),
) -> PlatformSettingsView:
    return _to_view(get_or_create_platform_settings(db, ctx.tenant_id))


class PlatformSettingsUpdate(BaseModel):
    live_weather_enabled: bool | None = None
    weather_site_lat: float | None = None
    weather_site_lon: float | None = None
    gateway_circuit_breaker_enabled: bool | None = None
    gateway_timeout_seconds: float | None = None
    gateway_failure_threshold: int | None = None
    gateway_cooldown_seconds: int | None = None
    sso_enabled: bool | None = None


@router.put("/settings", response_model=PlatformSettingsView)
def update_settings(
    body: PlatformSettingsUpdate,
    ctx: AuthContext = Depends(require_permission("manage:settings")),
    db: Session = Depends(db_session),
) -> PlatformSettingsView:
    row = get_or_create_platform_settings(db, ctx.tenant_id)

    if body.weather_site_lat is not None and not (-90.0 <= body.weather_site_lat <= 90.0):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "weather_site_lat must be between -90 and 90")
    if body.weather_site_lon is not None and not (-180.0 <= body.weather_site_lon <= 180.0):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "weather_site_lon must be between -180 and 180")
    if body.gateway_timeout_seconds is not None and not (1.0 <= body.gateway_timeout_seconds <= 300.0):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gateway_timeout_seconds must be between 1 and 300")
    if body.gateway_failure_threshold is not None and not (1 <= body.gateway_failure_threshold <= 50):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gateway_failure_threshold must be between 1 and 50")
    if body.gateway_cooldown_seconds is not None and not (5 <= body.gateway_cooldown_seconds <= 3600):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "gateway_cooldown_seconds must be between 5 and 3600")

    changed = body.model_dump(exclude_unset=True)
    if changed.get("live_weather_enabled") and (
        (body.weather_site_lat if body.weather_site_lat is not None else row.weather_site_lat) is None
        or (body.weather_site_lon if body.weather_site_lon is not None else row.weather_site_lon) is None
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "weather_site_lat/weather_site_lon are required to enable the live weather feed")

    for field, value in changed.items():
        setattr(row, field, value)
    row.updated_by = ctx.email

    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="configuration.updated", payload={"changed": changed},
    )
    db.commit()
    return _to_view(row)
