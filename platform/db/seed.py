"""Seed the reference demo tenant, portfolio and users.

Reference portfolio per BRD §Document assumptions: 5 solar farms, 3 wind
farms, 2 battery systems, industrial consumers, grid interconnection.
Run inside the api container (has reo_common + DB access):

    docker compose -f platform/infra/docker-compose.yml run --rm api python /app/platform/db/seed.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "reo_common"))

from reo_common.config import get_settings  # noqa: E402
from reo_common.db import SessionLocal, break_glass_cross_tenant  # noqa: E402
from reo_common.models import (  # noqa: E402
    Asset,
    Battery,
    Constraint,
    ObjectivePolicy,
    Portfolio,
    Role,
    Site,
    Tenant,
    User,
)
from reo_common.security import hash_password  # noqa: E402
from sqlalchemy import select  # noqa: E402

settings = get_settings()

DEMO_PASSWORD = "Password123!"  # dev/demo only — never used outside local seed data

DEMO_USERS = [
    ("viewer@demo-utility.test", "Demo Viewer", [Role.VIEWER.value]),
    ("operator@demo-utility.test", "Demo Operator", [Role.OPERATOR.value]),
    ("senior.operator@demo-utility.test", "Demo Senior Operator", [Role.SENIOR_OPERATOR.value]),
    ("portfolio.manager@demo-utility.test", "Demo Portfolio Manager", [Role.PORTFOLIO_MANAGER.value]),
    ("ot.admin@demo-utility.test", "Demo OT Admin", [Role.OT_ADMIN.value]),
    ("model.admin@demo-utility.test", "Demo Model Admin", [Role.MODEL_ADMIN.value]),
    ("tenant.admin@demo-utility.test", "Demo Tenant Admin", [Role.TENANT_ADMIN.value]),
    ("auditor@demo-utility.test", "Demo Auditor / DPO", [Role.AUDITOR_DPO.value]),
    ("platform.admin@demo-utility.test", "Demo Platform Admin", [Role.PLATFORM_ADMIN.value, Role.TENANT_ADMIN.value]),
]

SOLAR_FARMS = [
    ("Solway Solar Park A", "solar-site-1", 40_000),
    ("Solway Solar Park B", "solar-site-1", 35_000),
    ("Fenland Solar Cluster A", "solar-site-2", 25_000),
    ("Fenland Solar Cluster B", "solar-site-2", 20_000),
    ("Fenland Solar Cluster C", "solar-site-2", 15_000),
]
WIND_FARMS = [
    ("Highland Wind Farm 1", "wind-site-1", 50_000),
    ("Highland Wind Farm 2", "wind-site-1", 30_000),
    ("Coastal Wind Farm", "wind-site-2", 20_000),
]
CONSUMERS = [
    ("Industrial Estate Line 1", "industrial-site", 6_000),
    ("Industrial Estate Line 2", "industrial-site", 4_500),
    ("Industrial Estate Line 3", "industrial-site", 3_000),
    ("Data Centre Campus", "industrial-site", 8_000),
    ("Cold Storage Facility", "industrial-site", 2_500),
    ("Manufacturing Plant", "industrial-site", 5_000),
]

SITES = {
    "solar-site-1": "Solway Solar Park",
    "solar-site-2": "Fenland Solar Cluster",
    "wind-site-1": "Highland Wind Farm",
    "wind-site-2": "Coastal Wind Farm",
    "storage-grid-site": "Grid Interconnection & BESS Hub",
    "industrial-site": "Industrial Estate Consumers",
}


def main() -> None:
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug)).scalar_one_or_none()
            if tenant is not None:
                print(f"tenant '{settings.default_tenant_slug}' already exists — skipping seed")
                return

            tenant = Tenant(
                slug=settings.default_tenant_slug,
                name="Demo Utility (Reference Portfolio)",
                deployment_mode="pooled",
                data_residency="EU",
                timezone="Europe/London",
                market_area="GB",
                retention_years=7,
            )
            db.add(tenant)
            db.flush()

            for email, display_name, roles in DEMO_USERS:
                db.add(
                    User(
                        tenant_id=tenant.id, email=email, display_name=display_name,
                        hashed_password=hash_password(DEMO_PASSWORD), roles=roles,
                    )
                )

            portfolio = Portfolio(tenant_id=tenant.id, name="REO Reference Portfolio", description="Seeded demo portfolio: 5 solar, 3 wind, 2 BESS, industrial consumers, grid interconnection")
            db.add(portfolio)
            db.flush()

            site_objs: dict[str, Site] = {}
            for key, name in SITES.items():
                s = Site(tenant_id=tenant.id, portfolio_id=portfolio.id, name=name, market_area="GB")
                db.add(s)
                db.flush()
                site_objs[key] = s

            for name, site_key, capacity_kw in SOLAR_FARMS:
                db.add(Asset(tenant_id=tenant.id, site_id=site_objs[site_key].id, name=name, asset_type="solar",
                              rated_capacity_kw=capacity_kw, ramp_rate_kw_per_min=capacity_kw * 0.2))
            for name, site_key, capacity_kw in WIND_FARMS:
                db.add(Asset(tenant_id=tenant.id, site_id=site_objs[site_key].id, name=name, asset_type="wind",
                              rated_capacity_kw=capacity_kw, ramp_rate_kw_per_min=capacity_kw * 0.15))
            for name, site_key, capacity_kw in CONSUMERS:
                db.add(Asset(tenant_id=tenant.id, site_id=site_objs[site_key].id, name=name, asset_type="consumer",
                              rated_capacity_kw=capacity_kw, ramp_rate_kw_per_min=capacity_kw * 0.5))

            grid_asset = Asset(tenant_id=tenant.id, site_id=site_objs["storage-grid-site"].id, name="Primary Grid Interconnection",
                                asset_type="grid_interconnection", rated_capacity_kw=120_000, ramp_rate_kw_per_min=20_000)
            db.add(grid_asset)

            bess_specs = [("BESS Unit 1", 20_000, 40_000), ("BESS Unit 2", 10_000, 20_000)]
            for name, power_kw, energy_kwh in bess_specs:
                battery_asset = Asset(tenant_id=tenant.id, site_id=site_objs["storage-grid-site"].id, name=name,
                                       asset_type="battery", rated_capacity_kw=power_kw, ramp_rate_kw_per_min=power_kw)
                db.add(battery_asset)
                db.flush()
                db.add(Battery(tenant_id=tenant.id, asset_id=battery_asset.id, energy_capacity_kwh=energy_kwh,
                                power_limit_kw=power_kw, soc_min_pct=10, soc_max_pct=95, soc_current_pct=50,
                                soh_pct=100, round_trip_efficiency=0.92, degradation_cost_per_kwh_cycled=0.02,
                                warranty_cycles_remaining=4000))

            db.flush()
            db.add(Constraint(tenant_id=tenant.id, scope=f"asset:{grid_asset.id}", constraint_type="grid_import_export_limit",
                               expression={"max_import_kw": 120_000, "max_export_kw": 100_000}, is_hard=True, source="grid-code"))

            db.add(ObjectivePolicy(
                tenant_id=tenant.id, version=1,
                weights={"cost": 0.35, "imbalance": 0.15, "degradation": 0.1, "carbon": 0.15, "curtailment": 0.15, "reliability": 0.1},
                carbon_price_per_tonne=80.0, risk_aversion=0.2, combination_method="lexicographic_safety_then_weighted_sum",
                approved_by="seed-script", is_active=True,
            ))

            db.commit()
            print(f"seeded tenant '{settings.default_tenant_slug}' ({tenant.id}) with {len(DEMO_USERS)} users, "
                  f"{len(SOLAR_FARMS)+len(WIND_FARMS)+len(CONSUMERS)+len(bess_specs)+1} assets across {len(SITES)} sites")
            print(f"demo login: any email above / password '{DEMO_PASSWORD}' / tenant_slug '{settings.default_tenant_slug}'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
