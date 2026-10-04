"""Forecast criteria: operators either *accept* the criteria the platform
proposes or *set their own*.

Forecasts are physics-based baselines and, additionally, trained ML models
(reo_common/forecast_ml.py). Until an operator decides, the physics baseline
keeps driving forecasts and the proposal — together with how each trained
model compares to the baseline on held-out data — is shown here for them to
review. Every decision is audit-logged."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.forecast_ml import (
    PROPOSED_CRITERIA, effective_criteria, model_summary, train_tenant_models, validate_criteria,
)
from reo_common.platform_settings import get_or_create_platform_settings
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.canonical import Asset, ForecastModel
from output.audit import append_audit_event

from ..deps import db_session, require_permission

router = APIRouter(prefix="/forecasting", tags=["forecasting"])


class ForecastModelView(BaseModel):
    asset_id: str
    asset_name: str | None
    variable: str
    algorithm: str
    status: str  # trained | insufficient_data
    n_samples: int
    trained_at: str | None
    mae_ml: float | None
    mae_physics: float | None
    improvement_pct: float | None
    holdout_hours: int | None
    applied: bool  # whether the criteria currently in force use this model for the asset


class CriteriaValues(BaseModel):
    model_mode: str  # physics | ml | auto
    band_scale: float
    min_training_hours: int
    min_improvement_pct: float
    retrain_hours: int


class ForecastCriteriaView(BaseModel):
    status: str  # proposed | accepted | custom
    in_force: CriteriaValues  # what the forecaster applies right now
    proposal: CriteriaValues  # what the platform proposes (accepting adopts exactly this)
    decided_by: str | None
    decided_at: str | None
    note: str
    models: list[ForecastModelView]


def _view(db: Session, tenant_id: str) -> ForecastCriteriaView:
    settings_row = get_or_create_platform_settings(db, tenant_id)
    criteria = effective_criteria(settings_row.forecast_criteria)
    assets = {a.id: a for a in db.execute(select(Asset).where(Asset.tenant_id == tenant_id)).scalars()}
    models = []
    for row in db.execute(select(ForecastModel).where(ForecastModel.tenant_id == tenant_id)).scalars():
        summary = model_summary(row)
        improvement = summary["improvement_pct"]
        applied = (
            row.status == "trained" and criteria.model_mode != "physics"
            and (criteria.model_mode == "ml" or (improvement is not None and improvement >= criteria.min_improvement_pct))
        )
        asset = assets.get(row.asset_id)
        models.append(ForecastModelView(asset_name=asset.name if asset else None, applied=applied, **summary))
    models.sort(key=lambda m: (m.variable, m.asset_name or ""))
    return ForecastCriteriaView(
        status=criteria.status,
        in_force=CriteriaValues(
            model_mode=criteria.model_mode, band_scale=criteria.band_scale, min_training_hours=criteria.min_training_hours,
            min_improvement_pct=criteria.min_improvement_pct, retrain_hours=criteria.retrain_hours,
        ),
        proposal=CriteriaValues(**PROPOSED_CRITERIA),
        decided_by=criteria.decided_by, decided_at=criteria.decided_at, note=criteria.note, models=models,
    )


@router.get("/criteria", response_model=ForecastCriteriaView)
def get_criteria(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session),
) -> ForecastCriteriaView:
    return _view(db, ctx.tenant_id)


def _decide(db: Session, ctx: AuthContext, status_value: str, values: dict, event_type: str) -> ForecastCriteriaView:
    row = get_or_create_platform_settings(db, ctx.tenant_id)
    previous = effective_criteria(row.forecast_criteria)
    row.forecast_criteria = {
        "status": status_value, **values, "decided_by": ctx.email, "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    row.updated_by = ctx.email
    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email, event_type=event_type,
        payload={"from": {"status": previous.status, "model_mode": previous.model_mode, "band_scale": previous.band_scale}, "to": {"status": status_value, **values}},
    )
    db.commit()
    return _view(db, ctx.tenant_id)


@router.post("/criteria/accept", response_model=ForecastCriteriaView)
def accept_criteria(
    ctx: AuthContext = Depends(require_permission("manage:forecast_criteria")), db: Session = Depends(db_session),
) -> ForecastCriteriaView:
    """Adopt the platform's proposed criteria exactly as shown."""
    return _decide(db, ctx, "accepted", dict(PROPOSED_CRITERIA), "forecast.criteria_accepted")


class CriteriaUpdate(BaseModel):
    model_mode: str = PROPOSED_CRITERIA["model_mode"]
    band_scale: float = PROPOSED_CRITERIA["band_scale"]
    min_training_hours: int = PROPOSED_CRITERIA["min_training_hours"]
    min_improvement_pct: float = PROPOSED_CRITERIA["min_improvement_pct"]
    retrain_hours: int = PROPOSED_CRITERIA["retrain_hours"]


@router.put("/criteria", response_model=ForecastCriteriaView)
def set_criteria(
    body: CriteriaUpdate,
    ctx: AuthContext = Depends(require_permission("manage:forecast_criteria")), db: Session = Depends(db_session),
) -> ForecastCriteriaView:
    """Set the criteria yourself (overrides the proposal)."""
    try:
        values = validate_criteria(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _decide(db, ctx, "custom", values, "forecast.criteria_set")


@router.post("/retrain", response_model=ForecastCriteriaView)
def retrain_now(
    ctx: AuthContext = Depends(require_permission("manage:forecast_criteria")), db: Session = Depends(db_session),
) -> ForecastCriteriaView:
    """Retrain every asset's model from the history ingested so far (the
    optimizer also does this on its own cadence)."""
    criteria = effective_criteria(get_or_create_platform_settings(db, ctx.tenant_id).forecast_criteria)
    assets = db.execute(select(Asset).where(Asset.tenant_id == ctx.tenant_id, Asset.effective_to.is_(None))).scalars().all()
    train_tenant_models(db, ctx.tenant_id, list(assets), criteria)
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email, event_type="forecast.retrained",
        payload={"assets": len(assets)},
    )
    db.commit()
    return _view(db, ctx.tenant_id)
