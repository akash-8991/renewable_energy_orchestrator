"""Portfolio registry: a tenant can describe its own sites/assets/batteries
(previously only the demo seed script could), with the validation the
optimizer needs, audit logging, and retire-not-delete semantics."""

import contextlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers.portfolio_registry import (  # noqa: E402
    AssetIn, AssetPatch, BatterySpec, SiteIn, create_asset, create_site, list_assets, list_sites, update_asset,
)
from database.connection import reset_current_tenant, set_current_tenant  # noqa: E402
from models.canonical import AuditEvent, Battery  # noqa: E402
from reo_common.security import AuthContext, PERMISSIONS, Role  # noqa: E402


@contextlib.contextmanager
def _tenant(tenant_id: str):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def _ctx(tenant_id: str) -> AuthContext:
    return AuthContext(user_id="00000000-0000-0000-0000-000000000001", tenant_id=tenant_id, roles=["tenant_admin"], email="admin@test.example")


def test_a_tenant_registers_a_site_and_assets_including_a_battery(db_session, two_tenants):
    t1, _ = two_tenants
    ctx = _ctx(t1.id)
    with _tenant(t1.id):
        site = create_site(SiteIn(name="North Farm", latitude=54.1, longitude=-1.2), ctx=ctx, db=db_session)
        solar = create_asset(AssetIn(site_id=site.id, name="Solar A", asset_type="solar", rated_capacity_kw=5000), ctx=ctx, db=db_session)
        bess = create_asset(
            AssetIn(site_id=site.id, name="BESS 1", asset_type="battery", rated_capacity_kw=2000,
                    battery=BatterySpec(energy_capacity_kwh=4000, power_limit_kw=2000)),
            ctx=ctx, db=db_session,
        )
        assert solar.battery is None and bess.battery.energy_capacity_kwh == 4000
        assert {a.name for a in list_assets(ctx=ctx, db=db_session)} >= {"Solar A", "BESS 1"}
        assert [s.name for s in list_sites(ctx=ctx, db=db_session)] == ["North Farm"]
        assert db_session.execute(select(Battery).where(Battery.asset_id == bess.id)).scalar_one().soc_max_pct == 95.0
        events = db_session.execute(select(AuditEvent.event_type).where(AuditEvent.tenant_id == t1.id)).scalars().all()
        assert events.count("registry.asset_created") == 2 and "registry.site_created" in events


def test_the_registry_enforces_what_the_optimizer_requires(db_session, two_tenants):
    t1, _ = two_tenants
    ctx = _ctx(t1.id)
    with _tenant(t1.id):
        site = create_site(SiteIn(name="Hub"), ctx=ctx, db=db_session)

        def bad(**kw):
            with pytest.raises(HTTPException) as exc:
                create_asset(AssetIn(site_id=site.id, **kw), ctx=ctx, db=db_session)
            return exc.value.status_code

        assert bad(name="B", asset_type="battery", rated_capacity_kw=100) == 400  # battery without a spec
        assert bad(name="S", asset_type="solar", rated_capacity_kw=100, battery=BatterySpec(energy_capacity_kwh=1, power_limit_kw=1)) == 400
        assert bad(name="B2", asset_type="battery", rated_capacity_kw=100,
                   battery=BatterySpec(energy_capacity_kwh=10, power_limit_kw=500)) == 400  # power above rating
        assert bad(name="B3", asset_type="battery", rated_capacity_kw=100,
                   battery=BatterySpec(energy_capacity_kwh=10, power_limit_kw=100, soc_min_pct=90, soc_max_pct=20)) == 400

        create_asset(AssetIn(site_id=site.id, name="Grid", asset_type="grid_interconnection", rated_capacity_kw=10000), ctx=ctx, db=db_session)
        assert bad(name="Grid 2", asset_type="grid_interconnection", rated_capacity_kw=5000) == 409  # one grid interconnection only
        assert bad(name="Grid", asset_type="solar", rated_capacity_kw=1) == 409  # duplicate name

        with pytest.raises(HTTPException) as exc:
            create_asset(AssetIn(site_id="00000000-0000-0000-0000-000000000009", name="X", asset_type="solar", rated_capacity_kw=1), ctx=ctx, db=db_session)
        assert exc.value.status_code == 404


def test_a_site_in_another_tenant_cannot_be_used(db_session, two_tenants):
    t1, t2 = two_tenants
    with _tenant(t2.id):
        other_site = create_site(SiteIn(name="Theirs"), ctx=_ctx(t2.id), db=db_session)
    with _tenant(t1.id):
        with pytest.raises(HTTPException) as exc:
            create_asset(AssetIn(site_id=other_site.id, name="Sneaky", asset_type="solar", rated_capacity_kw=1), ctx=_ctx(t1.id), db=db_session)
        assert exc.value.status_code == 404


def test_retiring_keeps_the_asset_and_allows_replacing_the_grid_connection(db_session, two_tenants):
    t1, _ = two_tenants
    ctx = _ctx(t1.id)
    with _tenant(t1.id):
        site = create_site(SiteIn(name="Hub"), ctx=ctx, db=db_session)
        grid = create_asset(AssetIn(site_id=site.id, name="Grid v1", asset_type="grid_interconnection", rated_capacity_kw=10000), ctx=ctx, db=db_session)
        retired = update_asset(grid.id, AssetPatch(retired=True), ctx=ctx, db=db_session)
        assert retired.retired is True
        replacement = create_asset(AssetIn(site_id=site.id, name="Grid v2", asset_type="grid_interconnection", rated_capacity_kw=20000), ctx=ctx, db=db_session)
        assert replacement.id != grid.id
        assert update_asset(grid.id, AssetPatch(retired=False, rated_capacity_kw=11000), ctx=ctx, db=db_session).rated_capacity_kw == 11000


def test_who_may_manage_the_registry():
    assert "manage:assets" in PERMISSIONS[Role.TENANT_ADMIN]
    assert "manage:assets" in PERMISSIONS[Role.PORTFOLIO_MANAGER]
    for role in (Role.VIEWER, Role.OPERATOR, Role.AUDITOR_DPO):
        assert "manage:assets" not in PERMISSIONS[role]
