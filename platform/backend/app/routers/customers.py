"""Retail/individual customer directory + consumption insights — distinct
from the Asset-based "industrial consumer" decision history in actions.py/
decisions.py. See models/canonical.py's Customer/CustomerReading docstrings
and docs/DATA_INGESTION.md for what these are and how they're ingested.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from models.canonical import Customer, CustomerReading
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerSummary(BaseModel):
    id: str
    customer_ref: str
    customer_type: str
    region: str
    annual_consumption_kwh: float
    renewable_profile: str | None
    solar_capacity_kw: float
    wind_capacity_kw: float
    battery_installed: bool
    battery_capacity_kwh: float
    tariff_plan: str | None


@router.get("", response_model=list[CustomerSummary])
def list_customers(
    customer_type: str | None = Query(None),
    region: str | None = Query(None),
    customer_ref: str | None = Query(None, description="exact or partial match on the customer's source id, e.g. CUST_014"),
    limit: int = Query(200, le=1000),
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> list[CustomerSummary]:
    stmt = select(Customer).order_by(Customer.customer_ref).limit(limit)
    if customer_type:
        stmt = stmt.where(Customer.customer_type == customer_type)
    if region:
        stmt = stmt.where(Customer.region == region)
    if customer_ref:
        stmt = stmt.where(Customer.customer_ref.ilike(f"%{customer_ref}%"))
    rows = db.execute(stmt).scalars().all()
    return [
        CustomerSummary(
            id=c.id, customer_ref=c.customer_ref, customer_type=c.customer_type, region=c.region,
            annual_consumption_kwh=c.annual_consumption_kwh, renewable_profile=c.renewable_profile,
            solar_capacity_kw=c.solar_capacity_kw, wind_capacity_kw=c.wind_capacity_kw,
            battery_installed=c.battery_installed, battery_capacity_kwh=c.battery_capacity_kwh,
            tariff_plan=c.tariff_plan,
        )
        for c in rows
    ]


@router.get("/filters", response_model=dict[str, list[str]])
def list_customer_filters(
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> dict[str, list[str]]:
    """Distinct customer_type/region values actually present, so the
    frontend's filter dropdowns never show an option with zero results."""
    types = [r[0] for r in db.execute(select(Customer.customer_type).distinct().order_by(Customer.customer_type)).all()]
    regions = [r[0] for r in db.execute(select(Customer.region).distinct().order_by(Customer.region)).all()]
    return {"customer_types": types, "regions": regions}


class DailyPoint(BaseModel):
    date: str
    consumption_kwh: float
    solar_generation_kwh: float
    wind_generation_kwh: float
    net_grid_import_kwh: float
    export_kwh: float
    avg_energy_rate_gbp_kwh: float
    estimated_cost_gbp: float


class CustomerInsights(BaseModel):
    customer: CustomerSummary
    total_consumption_kwh: float
    total_renewable_generation_kwh: float
    renewable_share_pct: float
    total_estimated_cost_gbp: float
    avg_daily_cost_gbp: float
    days_covered: int
    daily: list[DailyPoint]


@router.get("/{customer_ref}/insights", response_model=CustomerInsights)
def get_customer_insights(
    customer_ref: str,
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> CustomerInsights:
    customer = db.execute(select(Customer).where(Customer.customer_ref == customer_ref)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no customer with customer_ref={customer_ref!r}")

    stmt = select(CustomerReading).where(CustomerReading.customer_id == customer.id).order_by(CustomerReading.event_time)
    if since:
        stmt = stmt.where(CustomerReading.event_time >= since)
    if until:
        stmt = stmt.where(CustomerReading.event_time <= until)
    readings = db.execute(stmt).scalars().all()

    total_consumption = sum(r.consumption_kwh for r in readings)
    total_renewable = sum(r.solar_generation_kwh + r.wind_generation_kwh for r in readings)
    total_cost = sum(r.estimated_cost_gbp for r in readings)

    return CustomerInsights(
        customer=CustomerSummary(
            id=customer.id, customer_ref=customer.customer_ref, customer_type=customer.customer_type,
            region=customer.region, annual_consumption_kwh=customer.annual_consumption_kwh,
            renewable_profile=customer.renewable_profile, solar_capacity_kw=customer.solar_capacity_kw,
            wind_capacity_kw=customer.wind_capacity_kw, battery_installed=customer.battery_installed,
            battery_capacity_kwh=customer.battery_capacity_kwh, tariff_plan=customer.tariff_plan,
        ),
        total_consumption_kwh=total_consumption,
        total_renewable_generation_kwh=total_renewable,
        renewable_share_pct=(total_renewable / total_consumption * 100) if total_consumption else 0.0,
        total_estimated_cost_gbp=total_cost,
        avg_daily_cost_gbp=(total_cost / len(readings)) if readings else 0.0,
        days_covered=len(readings),
        daily=[
            DailyPoint(
                date=r.event_time.date().isoformat(), consumption_kwh=r.consumption_kwh,
                solar_generation_kwh=r.solar_generation_kwh, wind_generation_kwh=r.wind_generation_kwh,
                net_grid_import_kwh=r.net_grid_import_kwh, export_kwh=r.export_kwh,
                avg_energy_rate_gbp_kwh=r.avg_energy_rate_gbp_kwh, estimated_cost_gbp=r.estimated_cost_gbp,
            )
            for r in readings
        ],
    )
