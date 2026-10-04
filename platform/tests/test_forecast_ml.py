"""Trained ML forecasting + operator-governed criteria
(reo_common/forecast_ml.py, policy/forecast.py, routers/forecasting.py)."""

import contextlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent / "policy"))

from app.routers.forecasting import CriteriaUpdate, accept_criteria, get_criteria, retrain_now, set_criteria  # noqa: E402
from database.connection import break_glass_cross_tenant, reset_current_tenant, set_current_tenant  # noqa: E402
from forecast import forecast_asset  # noqa: E402
from models.canonical import Asset, AuditEvent, ForecastModel, Portfolio, Site, Telemetry  # noqa: E402
from reo_common.forecast_ml import (  # noqa: E402
    PROPOSED_CRITERIA, TrainedModel, effective_criteria, ensure_models_fresh, load_trained_models, models_to_apply,
    physics_mean_kw, train_model, train_tenant_models, validate_criteria,
)
from reo_common.security import AuthContext  # noqa: E402

START = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def _history(variable: str, rated: float, days: int, truth) -> list[tuple[datetime, float]]:
    return [(START + timedelta(hours=h), truth(START + timedelta(hours=h))) for h in range(days * 24)]


def _solar_truth(rated):
    # this site underperforms the textbook curve: 65% of it, with a one-hour-later peak
    return lambda t: 0.65 * physics_mean_kw("solar", rated, t + timedelta(hours=-1))


# --- the model ---------------------------------------------------------------


def test_ml_beats_the_physics_baseline_on_a_site_that_deviates_from_it():
    model = train_model("solar", 1000.0, _history("solar", 1000.0, 20, _solar_truth(1000.0)), min_hours=168)
    assert model is not None
    assert model.mae_ml < model.mae_physics
    assert model.improvement_pct > 20.0


def test_training_is_deterministic():
    data = _history("solar", 1000.0, 14, _solar_truth(1000.0))
    a, b = train_model("solar", 1000.0, data, min_hours=168), train_model("solar", 1000.0, data, min_hours=168)
    assert a.coef == b.coef and a.q10_by_hour == b.q10_by_hour and a.improvement_pct == b.improvement_pct


def test_not_enough_history_means_no_model():
    assert train_model("solar", 1000.0, _history("solar", 1000.0, 3, _solar_truth(1000.0)), min_hours=168) is None


def test_solar_is_zero_at_night_whatever_the_model_says():
    model = train_model("solar", 1000.0, _history("solar", 1000.0, 14, _solar_truth(1000.0)), min_hours=168)
    midnight = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    assert model.predict_mean(midnight, physics_mean_kw("solar", 1000.0, midnight)) == 0.0


def test_predictions_never_exceed_rated_capacity_or_go_negative():
    model = train_model("wind", 900.0, _history("wind", 900.0, 14, lambda t: 700.0), min_hours=168)
    for hour in range(24):
        t = datetime(2026, 9, 20, hour, 0, tzinfo=timezone.utc)
        assert 0.0 <= model.predict_mean(t, physics_mean_kw("wind", 900.0, t)) <= 900.0


def test_band_is_calibrated_from_held_out_residuals_and_ordered():
    model = train_model("solar", 1000.0, _history("solar", 1000.0, 20, _solar_truth(1000.0)), min_hours=168)
    assert len(model.q10_by_hour) == len(model.q90_by_hour) == 24
    assert all(lo <= hi for lo, hi in zip(model.q10_by_hour, model.q90_by_hour))


def test_a_model_round_trips_through_its_stored_form():
    model = train_model("solar", 1000.0, _history("solar", 1000.0, 14, _solar_truth(1000.0)), min_hours=168)
    again = TrainedModel.from_row("solar", model.n_samples, model.to_params(), model.to_metrics())
    t = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    assert again.predict_mean(t, 500.0) == model.predict_mean(t, 500.0)


# --- criteria ---------------------------------------------------------------


def test_nothing_decided_means_the_physics_baseline_stays_in_use():
    for stored in (None, {}, {"status": "proposed"}):
        c = effective_criteria(stored)
        assert (c.status, c.model_mode, c.band_scale) == ("proposed", "physics", 1.0)


def test_accepting_adopts_the_proposal_and_setting_overrides_it():
    accepted = effective_criteria({"status": "accepted", **PROPOSED_CRITERIA})
    assert (accepted.status, accepted.model_mode) == ("accepted", "auto")
    custom = effective_criteria({"status": "custom", "model_mode": "ml", "band_scale": 1.5})
    assert (custom.model_mode, custom.band_scale, custom.min_training_hours) == ("ml", 1.5, PROPOSED_CRITERIA["min_training_hours"])


def test_criteria_are_range_checked():
    assert validate_criteria({"model_mode": "ml", "band_scale": 2})["band_scale"] == 2.0
    for bad in ({"model_mode": "magic"}, {"band_scale": 99}, {"band_scale": "x"}, {"min_training_hours": 1}, {"retrain_hours": 0}, {"min_improvement_pct": 150}):
        with pytest.raises(ValueError):
            validate_criteria(bad)


def _model(improvement: float) -> TrainedModel:
    return TrainedModel("solar", 1000.0, 1000.0, [0.0] * 15, [0.0] * 24, [0.0] * 24, 500, 1.0, 2.0, improvement, 100)


def test_which_models_each_mode_applies():
    models = {"good": _model(40.0), "meh": _model(2.0)}
    physics = effective_criteria({"status": "custom", "model_mode": "physics"})
    ml = effective_criteria({"status": "custom", "model_mode": "ml"})
    auto = effective_criteria({"status": "custom", "model_mode": "auto", "min_improvement_pct": 5})
    assert models_to_apply(models, physics) == {}
    assert set(models_to_apply(models, ml)) == {"good", "meh"}
    assert set(models_to_apply(models, auto)) == {"good"}


# --- forecast generation ----------------------------------------------------


def _asset(kind="solar", rated=1000.0) -> Asset:
    return Asset(id=str(uuid.uuid4()), tenant_id="t", site_id="s", name=f"{kind}-1", asset_type=kind, rated_capacity_kw=rated)


def _spread(points) -> float:
    by_q = {(p.valid_time, p.quantile): p.value for p in points}
    return sum(by_q[(t, 0.9)] - by_q[(t, 0.1)] for t in {p.valid_time for p in points})


def test_a_wider_band_scale_widens_the_baseline_band():
    asset, now = _asset(), datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    narrow = forecast_asset(asset, 24, 1.0, now, criteria=effective_criteria({"status": "custom", "band_scale": 1.0}))
    wide = forecast_asset(asset, 24, 1.0, now, criteria=effective_criteria({"status": "custom", "band_scale": 2.0}))
    assert _spread(wide) > _spread(narrow) * 1.5
    assert {p.value for p in narrow if p.quantile == 0.5} == {p.value for p in wide if p.quantile == 0.5}  # the median doesn't move


def test_the_ml_path_is_used_when_given_a_model_and_marked_as_such():
    asset, now = _asset(), datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    model = train_model("solar", 1000.0, _history("solar", 1000.0, 20, _solar_truth(1000.0)), min_hours=168)
    ml_points = forecast_asset(asset, 24, 1.0, now, ml_model=model, criteria=effective_criteria({"status": "custom", "model_mode": "ml"}))
    base_points = forecast_asset(asset, 24, 1.0, now)
    assert all(p.used_ml for p in ml_points) and not any(p.used_ml for p in base_points)
    by_q = {(p.valid_time, p.quantile): p.value for p in ml_points}
    assert all(by_q[(t, 0.1)] <= by_q[(t, 0.5)] <= by_q[(t, 0.9)] for t in {p.valid_time for p in ml_points})
    # the learned site is ~35% below the textbook curve; the ML forecast reflects that
    ml_total = sum(v for (t, q), v in by_q.items() if q == 0.5)
    base_total = sum(p.value for p in base_points if p.quantile == 0.5)
    assert ml_total < base_total * 0.85


# --- persistence + API ------------------------------------------------------


@contextlib.contextmanager
def _tenant(tenant_id: str):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def _ctx(tenant_id: str, email="operator@test.example") -> AuthContext:
    return AuthContext(user_id="00000000-0000-0000-0000-000000000001", tenant_id=tenant_id, roles=["operator"], email=email)


def _seed_solar_site(db, tenant_id: str, days: int) -> Asset:
    portfolio = db.execute(select(Portfolio).where(Portfolio.tenant_id == tenant_id)).scalars().first()
    site = Site(tenant_id=tenant_id, portfolio_id=portfolio.id, name="Test site")
    db.add(site)
    db.flush()
    asset = Asset(tenant_id=tenant_id, site_id=site.id, name="Test solar", asset_type="solar", rated_capacity_kw=1000.0)
    db.add(asset)
    db.flush()
    for t, v in _history("solar", 1000.0, days, _solar_truth(1000.0)):
        db.add(Telemetry(tenant_id=tenant_id, asset_id=asset.id, metric="power_kw", event_time=t, value=v, unit="kW", source="test"))
    db.flush()
    return asset


def test_training_from_ingested_history_persists_a_model_and_reports_thin_history(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        asset = _seed_solar_site(db_session, t1.id, days=14)
        crit = effective_criteria(None)
        rows = train_tenant_models(db_session, t1.id, [asset], crit)
        assert [(r.variable, r.status) for r in rows] == [("solar", "trained")]
        assert rows[0].metrics["improvement_pct"] > 20
        assert asset.id in load_trained_models(db_session, t1.id)

        # a fresh model is not retrained until retrain_hours has passed
        assert ensure_models_fresh(db_session, t1.id, [asset], crit) is False
        assert ensure_models_fresh(db_session, t1.id, [asset], crit, now=datetime.now(timezone.utc) + timedelta(hours=25)) is True


def test_too_little_history_is_recorded_as_insufficient_data(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        asset = _seed_solar_site(db_session, t1.id, days=2)
        rows = train_tenant_models(db_session, t1.id, [asset], effective_criteria(None))
        assert rows[0].status == "insufficient_data" and rows[0].n_samples == 48
        assert load_trained_models(db_session, t1.id) == {}


def test_operator_flow_default_then_accept_then_set(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        view = get_criteria(ctx=_ctx(t1.id), db=db_session)
        assert view.status == "proposed" and view.in_force.model_mode == "physics"
        assert view.proposal.model_mode == "auto"

        view = accept_criteria(ctx=_ctx(t1.id), db=db_session)
        assert (view.status, view.in_force.model_mode, view.decided_by) == ("accepted", "auto", "operator@test.example")

        view = set_criteria(CriteriaUpdate(model_mode="ml", band_scale=1.4, min_training_hours=96, min_improvement_pct=3, retrain_hours=12),
                            ctx=_ctx(t1.id), db=db_session)
        assert (view.status, view.in_force.model_mode, view.in_force.band_scale, view.in_force.retrain_hours) == ("custom", "ml", 1.4, 12)

        events = db_session.execute(select(AuditEvent.event_type).where(AuditEvent.tenant_id == t1.id)).scalars().all()
        assert "forecast.criteria_accepted" in events and "forecast.criteria_set" in events


def test_invalid_operator_criteria_are_rejected_without_changing_anything(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        with pytest.raises(HTTPException) as exc:
            set_criteria(CriteriaUpdate(model_mode="ml", band_scale=50), ctx=_ctx(t1.id), db=db_session)
        assert exc.value.status_code == 400
        assert get_criteria(ctx=_ctx(t1.id), db=db_session).status == "proposed"


def test_retrain_now_trains_and_reports_models(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        _seed_solar_site(db_session, t1.id, days=14)
        view = retrain_now(ctx=_ctx(t1.id), db=db_session)
        assert [(m.variable, m.status, m.applied) for m in view.models] == [("solar", "trained", False)]  # physics still in force: not applied
        accept_criteria(ctx=_ctx(t1.id), db=db_session)
        assert get_criteria(ctx=_ctx(t1.id), db=db_session).models[0].applied is True  # auto + a 30%+ improvement


def test_operators_hold_the_forecast_criteria_permission():
    from reo_common.security import PERMISSIONS, Role

    for role in (Role.OPERATOR, Role.SENIOR_OPERATOR, Role.PORTFOLIO_MANAGER, Role.MODEL_ADMIN, Role.TENANT_ADMIN):
        assert "manage:forecast_criteria" in PERMISSIONS[role]
    assert "manage:forecast_criteria" not in PERMISSIONS[Role.VIEWER]
    assert "manage:forecast_criteria" not in PERMISSIONS[Role.AUDITOR_DPO]
