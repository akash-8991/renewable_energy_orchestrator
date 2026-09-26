"""Configuration Studio (backend/app/routers/configuration.py) — the
runtime-configurable settings behind the production-readiness punch list.
A tenant's first read must create sane defaults; updates must validate
range, require the site coordinates before the weather feed can be turned
on, and be audit-logged."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers.configuration import PlatformSettingsUpdate, get_settings_endpoint, update_settings  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from reo_common.security import AuthContext  # noqa: E402
from database.connection import reset_current_tenant, set_current_tenant  # noqa: E402
import contextlib  # noqa: E402


@contextlib.contextmanager
def _tenant_context(tenant_id: str):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def _ctx(tenant_id: str) -> AuthContext:
    return AuthContext(
        user_id="00000000-0000-0000-0000-000000000001", tenant_id=tenant_id,
        roles=["tenant_admin"], email="admin@test.example",
    )


def test_first_read_creates_default_settings(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        view = get_settings_endpoint(ctx=_ctx(t1.id), db=db_session)
    assert view.live_weather_enabled is False
    assert view.gateway_circuit_breaker_enabled is True
    assert view.gateway_timeout_seconds == 30.0
    assert view.sso_enabled is False


def test_update_persists_and_returns_new_values(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        update_settings(
            PlatformSettingsUpdate(gateway_timeout_seconds=45.0, gateway_failure_threshold=5),
            ctx=_ctx(t1.id), db=db_session,
        )
        view = get_settings_endpoint(ctx=_ctx(t1.id), db=db_session)
    assert view.gateway_timeout_seconds == 45.0
    assert view.gateway_failure_threshold == 5
    assert view.updated_by == "admin@test.example"


def test_update_rejects_out_of_range_timeout(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        with pytest.raises(HTTPException) as exc_info:
            update_settings(PlatformSettingsUpdate(gateway_timeout_seconds=999.0), ctx=_ctx(t1.id), db=db_session)
    assert exc_info.value.status_code == 400


def test_update_rejects_invalid_latitude(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        with pytest.raises(HTTPException) as exc_info:
            update_settings(PlatformSettingsUpdate(weather_site_lat=200.0), ctx=_ctx(t1.id), db=db_session)
    assert exc_info.value.status_code == 400


def test_enabling_weather_feed_requires_coordinates(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        with pytest.raises(HTTPException) as exc_info:
            update_settings(PlatformSettingsUpdate(live_weather_enabled=True), ctx=_ctx(t1.id), db=db_session)
    assert exc_info.value.status_code == 400


def test_enabling_weather_feed_succeeds_with_coordinates(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        view = update_settings(
            PlatformSettingsUpdate(live_weather_enabled=True, weather_site_lat=51.5, weather_site_lon=-0.1),
            ctx=_ctx(t1.id), db=db_session,
        )
    assert view.live_weather_enabled is True
    assert view.weather_site_lat == 51.5


def test_tenants_are_isolated(db_session, two_tenants):
    t1, t2 = two_tenants
    with _tenant_context(t1.id):
        update_settings(PlatformSettingsUpdate(gateway_timeout_seconds=99.0), ctx=_ctx(t1.id), db=db_session)
    with _tenant_context(t2.id):
        view2 = get_settings_endpoint(ctx=_ctx(t2.id), db=db_session)
    assert view2.gateway_timeout_seconds == 30.0  # untouched default, not tenant 1's value
