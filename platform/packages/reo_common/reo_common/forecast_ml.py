"""Trained ML forecasting, and the operator-governed criteria that decide
when it is used.

Forecasts start from a physics-based baseline (diurnal solar curve, wind
power curve, daily demand/price shapes — `physics_mean_kw`). On top of that
the platform trains a small ML model per asset from the telemetry it has
actually ingested: a ridge regression on cyclical time features (hour of
day, day of year, weekend) *plus the physics baseline as an input feature*,
so the model learns how this site really behaves relative to the textbook
curve, and falls back to the textbook curve wherever it has nothing better.
Its uncertainty band comes from the model's own held-out residuals, per hour
of day — not a fixed percentage.

Nothing here samples randomly: training is a closed-form least-squares fit,
so the same history always produces the same model and the same forecast.

Operators stay in control (`ForecastCriteria`). Until an operator either
*accepts* the proposed criteria or *sets their own*, forecasts keep using
the physics baseline exactly as before — a freshly trained model never takes
over on its own. The evaluation (held-out error of ML vs physics) is shown to
the operator so the decision is an informed one.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger("reo.forecast_ml")

ALGORITHM = "ridge-harmonic-v1"
ML_MODEL_VERSION = "ml-ridge-v1"
PHYSICS_MODEL_VERSION = "baseline-v1"

VARIABLES = {"solar": "solar", "wind": "wind", "consumer": "demand", "grid_interconnection": "price"}
TRAINING_METRIC = {"solar": "power_kw", "wind": "power_kw", "demand": "power_kw", "price": "market_price_gbp_per_mwh"}

MODES = ("physics", "ml", "auto")


# ---------------------------------------------------------------------------
# Physics-based baseline (no weather) — also the feature the ML model builds on
# ---------------------------------------------------------------------------


def solar_diurnal_factor(hour: float) -> float:
    if hour < 6 or hour > 20:
        return 0.0
    return max(0.0, math.sin(math.pi * (hour - 6) / 14)) ** 1.5


def wind_power_factor(speed_ms: float) -> float:
    if speed_ms < 3 or speed_ms > 25:
        return 0.0
    if speed_ms >= 12:
        return 1.0
    return ((speed_ms - 3) / 9) ** 3


def physics_mean_kw(variable: str, rated_kw: float, valid_time: datetime) -> float:
    """The physics-based mean for `variable` at `valid_time` (kW, or GBP/MWh
    for price) without live weather."""
    hour = valid_time.hour + valid_time.minute / 60
    if variable == "solar":
        return rated_kw * solar_diurnal_factor(hour) * 0.92
    if variable == "wind":
        base_speed = 8.0 + 1.5 * math.sin(math.pi * hour / 12)
        return rated_kw * wind_power_factor(base_speed) * 0.9
    if variable == "demand":
        return rated_kw * (0.5 + 0.3 * math.sin(math.pi * (hour - 7) / 12) ** 2)
    if variable == "price":
        return 65.0 + 20 * math.sin(math.pi * (hour - 6) / 12)
    raise ValueError(f"unknown forecast variable {variable!r}")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _features(valid_time: datetime, baseline: float, scale: float) -> list[float]:
    hour = valid_time.hour + valid_time.minute / 60
    doy = valid_time.timetuple().tm_yday
    row = [1.0]
    for k in (1, 2, 3, 4):
        row += [math.sin(2 * math.pi * k * hour / 24), math.cos(2 * math.pi * k * hour / 24)]
    for k in (1, 2):
        row += [math.sin(2 * math.pi * k * doy / 365.25), math.cos(2 * math.pi * k * doy / 365.25)]
    row.append(1.0 if valid_time.weekday() >= 5 else 0.0)
    row.append(baseline / scale)
    return row


@dataclass
class TrainedModel:
    variable: str
    rated_kw: float
    scale: float
    coef: list[float]
    q10_by_hour: list[float]  # held-out residual (actual - predicted) 10th percentile, per hour of day
    q90_by_hour: list[float]
    n_samples: int
    mae_ml: float
    mae_physics: float
    improvement_pct: float
    holdout_hours: int
    algorithm: str = ALGORITHM

    def predict_mean(self, valid_time: datetime, baseline: float) -> float:
        x = _features(valid_time, baseline, self.scale)
        value = sum(c * v for c, v in zip(self.coef, x)) * self.scale
        if self.variable == "price":
            return value
        if self.variable == "solar" and baseline <= 0.0:
            return 0.0  # the sun is down — no model gets to forecast solar output at night
        return min(max(0.0, value), self.rated_kw)

    def band(self, valid_time: datetime, lead_hours: float, band_scale: float) -> tuple[float, float]:
        """(lower, upper) offsets from the mean. Widens gently with lead time
        and scales with the operator's `band_scale`."""
        hour = valid_time.hour
        widen = band_scale * (1.0 + 0.02 * max(0.0, lead_hours - 1.0))
        return self.q10_by_hour[hour] * widen, self.q90_by_hour[hour] * widen

    def to_params(self) -> dict:
        return {"scale": self.scale, "rated_kw": self.rated_kw, "coef": self.coef,
                "q10_by_hour": self.q10_by_hour, "q90_by_hour": self.q90_by_hour}

    def to_metrics(self) -> dict:
        return {"mae_ml": self.mae_ml, "mae_physics": self.mae_physics,
                "improvement_pct": self.improvement_pct, "holdout_hours": self.holdout_hours}

    @classmethod
    def from_row(cls, variable: str, n_samples: int, params: dict, metrics: dict, algorithm: str = ALGORITHM) -> "TrainedModel":
        return cls(
            variable=variable, rated_kw=params["rated_kw"], scale=params["scale"], coef=params["coef"],
            q10_by_hour=params["q10_by_hour"], q90_by_hour=params["q90_by_hour"], n_samples=n_samples,
            mae_ml=metrics["mae_ml"], mae_physics=metrics["mae_physics"], improvement_pct=metrics["improvement_pct"],
            holdout_hours=metrics["holdout_hours"], algorithm=algorithm,
        )


def _ridge_fit(x_rows: list[list[float]], y: list[float], l2: float = 1.0) -> list[float]:
    import numpy as np

    x = np.asarray(x_rows, dtype=float)
    target = np.asarray(y, dtype=float)
    penalty = np.eye(x.shape[1]) * l2
    penalty[0, 0] = 0.0  # the intercept is not shrunk
    return np.linalg.solve(x.T @ x + penalty, x.T @ target).tolist()


def _percentile(values: list[float], q: float) -> float:
    import numpy as np

    return float(np.percentile(np.asarray(values, dtype=float), q))


def train_model(
    variable: str, rated_kw: float, samples: list[tuple[datetime, float]], *, min_hours: int,
) -> TrainedModel | None:
    """Fit on hourly `(hour_start, value)` samples. Returns None when there is
    not enough history (`len(samples) < min_hours`).

    The last 20% (at least 24 hours) is held out: a model fitted on the rest
    is scored there against the physics baseline (the evaluation shown to the
    operator) and its residuals calibrate the uncertainty band. The deployed
    model is then refitted on all of the history."""
    samples = sorted(samples, key=lambda s: s[0])
    n = len(samples)
    if n < max(min_hours, 48):
        return None

    scale = max(rated_kw, 1.0) if variable != "price" else 100.0
    baselines = [physics_mean_kw(variable, rated_kw, t) for t, _ in samples]
    x_all = [_features(t, b, scale) for (t, _), b in zip(samples, baselines)]
    y_all = [v / scale for _, v in samples]

    holdout = max(24, n // 5)
    split = n - holdout
    eval_coef = _ridge_fit(x_all[:split], y_all[:split])
    eval_model = TrainedModel(variable, rated_kw, scale, eval_coef, [0.0] * 24, [0.0] * 24, split, 0.0, 0.0, 0.0, holdout)

    ml_abs_err, phys_abs_err = [], []
    residual_by_hour: list[list[float]] = [[] for _ in range(24)]
    all_residuals: list[float] = []
    for i in range(split, n):
        t, actual = samples[i]
        predicted = eval_model.predict_mean(t, baselines[i])
        residual = actual - predicted
        ml_abs_err.append(abs(residual))
        phys_abs_err.append(abs(actual - baselines[i]))
        residual_by_hour[t.hour].append(residual)
        all_residuals.append(residual)

    mae_ml = sum(ml_abs_err) / len(ml_abs_err)
    mae_phys = sum(phys_abs_err) / len(phys_abs_err)
    improvement = (mae_phys - mae_ml) / mae_phys * 100.0 if mae_phys > 1e-9 else 0.0

    global_q10, global_q90 = _percentile(all_residuals, 10), _percentile(all_residuals, 90)
    q10 = [_percentile(r, 10) if len(r) >= 5 else global_q10 for r in residual_by_hour]
    q90 = [_percentile(r, 90) if len(r) >= 5 else global_q90 for r in residual_by_hour]

    final_coef = _ridge_fit(x_all, y_all)
    return TrainedModel(
        variable=variable, rated_kw=rated_kw, scale=scale, coef=final_coef, q10_by_hour=q10, q90_by_hour=q90,
        n_samples=n, mae_ml=round(mae_ml, 4), mae_physics=round(mae_phys, 4), improvement_pct=round(improvement, 2),
        holdout_hours=holdout,
    )


# ---------------------------------------------------------------------------
# Operator criteria
# ---------------------------------------------------------------------------

PROPOSED_CRITERIA = {
    "model_mode": "auto",  # use the trained model per asset only where it beats the physics baseline
    "band_scale": 1.0,
    "min_training_hours": 168,  # one week of hourly history before a model is trusted at all
    "min_improvement_pct": 5.0,  # "auto": the model must cut held-out error by at least this much
    "retrain_hours": 24,
}


@dataclass
class EffectiveCriteria:
    """What the forecaster actually applies right now."""
    status: str  # proposed | accepted | custom
    model_mode: str
    band_scale: float
    min_training_hours: int
    min_improvement_pct: float
    retrain_hours: int
    decided_by: str | None = None
    decided_at: str | None = None
    note: str = field(default="")


def effective_criteria(stored: dict | None) -> EffectiveCriteria:
    """Until an operator accepts or sets criteria, the physics baseline is
    what drives forecasts (status "proposed" applies mode "physics" — the
    proposal is shown, not silently adopted)."""
    stored = stored or {}
    status = stored.get("status", "proposed")
    if status == "proposed":
        return EffectiveCriteria(
            status="proposed", model_mode="physics", band_scale=1.0,
            min_training_hours=PROPOSED_CRITERIA["min_training_hours"],
            min_improvement_pct=PROPOSED_CRITERIA["min_improvement_pct"],
            retrain_hours=PROPOSED_CRITERIA["retrain_hours"],
            note="awaiting an operator to accept the proposed criteria or set their own; physics baseline in use",
        )
    merged = {**PROPOSED_CRITERIA, **{k: v for k, v in stored.items() if k in PROPOSED_CRITERIA}}
    return EffectiveCriteria(
        status=status, model_mode=merged["model_mode"], band_scale=float(merged["band_scale"]),
        min_training_hours=int(merged["min_training_hours"]), min_improvement_pct=float(merged["min_improvement_pct"]),
        retrain_hours=int(merged["retrain_hours"]), decided_by=stored.get("decided_by"), decided_at=stored.get("decided_at"),
    )


def validate_criteria(values: dict) -> dict:
    """Range-check operator-supplied criteria; returns the cleaned dict or
    raises ValueError with a message safe to show the operator."""
    mode = values.get("model_mode", PROPOSED_CRITERIA["model_mode"])
    if mode not in MODES:
        raise ValueError(f"model_mode must be one of {', '.join(MODES)}")
    out = {"model_mode": mode}
    for key, lo, hi, cast in (
        ("band_scale", 0.25, 4.0, float), ("min_training_hours", 48, 8760, int),
        ("min_improvement_pct", 0.0, 100.0, float), ("retrain_hours", 1, 720, int),
    ):
        raw = values.get(key, PROPOSED_CRITERIA[key])
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None
        if not (lo <= value <= hi):
            raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
        out[key] = value
    return out


def models_to_apply(models: dict[str, TrainedModel], criteria: EffectiveCriteria) -> dict[str, TrainedModel]:
    """asset_id -> the trained model the forecaster should use for it (assets
    absent from the result use the physics baseline)."""
    if criteria.model_mode == "physics":
        return {}
    if criteria.model_mode == "ml":
        return dict(models)
    return {a: m for a, m in models.items() if m.improvement_pct >= criteria.min_improvement_pct}  # auto


# ---------------------------------------------------------------------------
# Training from the platform's own telemetry, and persistence
# ---------------------------------------------------------------------------


def _hourly_samples(db, tenant_id: str) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    from sqlalchemy import func, select

    from models.canonical import Telemetry

    hour = func.date_trunc("hour", Telemetry.event_time)
    rows = db.execute(
        select(Telemetry.asset_id, Telemetry.metric, hour.label("h"), func.avg(Telemetry.value))
        .where(Telemetry.tenant_id == tenant_id, Telemetry.metric.in_(set(TRAINING_METRIC.values())), Telemetry.quality != "bad")
        .group_by(Telemetry.asset_id, Telemetry.metric, hour)
    ).all()
    out: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for asset_id, metric, h, value in rows:
        out.setdefault((asset_id, metric), []).append((h, float(value)))
    return out


def train_tenant_models(db, tenant_id: str, assets: list, criteria: EffectiveCriteria, now: datetime | None = None) -> list:
    """(Re)train every forecastable asset's model from its ingested history and
    upsert the `forecast_models` rows. An asset without enough history gets an
    "insufficient_data" row recording how much it has. Runs regardless of the
    operator's chosen mode: the evaluation is what lets them choose."""
    from sqlalchemy import select

    from models.canonical import ForecastModel

    now = now or datetime.now(timezone.utc)
    history = _hourly_samples(db, tenant_id)
    existing = {(m.asset_id, m.variable): m for m in db.execute(select(ForecastModel).where(ForecastModel.tenant_id == tenant_id)).scalars()}
    rows = []
    for asset in assets:
        variable = VARIABLES.get(asset.asset_type)
        if variable is None:
            continue
        samples = history.get((asset.id, TRAINING_METRIC[variable]), [])
        if variable == "demand":
            samples = [(t, abs(v)) for t, v in samples]  # consumer load is reported as negative power
        model = train_model(variable, asset.rated_capacity_kw, samples, min_hours=criteria.min_training_hours)
        row = existing.get((asset.id, variable))
        if row is None:
            row = ForecastModel(tenant_id=tenant_id, asset_id=asset.id, variable=variable)
            db.add(row)
        row.algorithm = ALGORITHM
        row.trained_at = now
        if model is None:
            row.status, row.n_samples, row.metrics, row.params = "insufficient_data", len(samples), {}, {}
        else:
            row.status, row.n_samples, row.metrics, row.params = "trained", model.n_samples, model.to_metrics(), model.to_params()
        rows.append(row)
    db.flush()
    return rows


def load_trained_models(db, tenant_id: str) -> dict[str, TrainedModel]:
    from sqlalchemy import select

    from models.canonical import ForecastModel

    out = {}
    for row in db.execute(select(ForecastModel).where(ForecastModel.tenant_id == tenant_id, ForecastModel.status == "trained")).scalars():
        try:
            out[row.asset_id] = TrainedModel.from_row(row.variable, row.n_samples, row.params, row.metrics, row.algorithm)
        except (KeyError, TypeError):
            log.warning("forecast model %s has unreadable params, ignoring it", row.id)
    return out


def ensure_models_fresh(db, tenant_id: str, assets: list, criteria: EffectiveCriteria, now: datetime | None = None) -> bool:
    """Retrain when any asset has no model row yet, a model is older than the
    criteria's `retrain_hours`, or an "insufficient_data" row is over 15
    minutes old (new history may have arrived). Returns True if it trained."""
    from sqlalchemy import select

    from models.canonical import ForecastModel

    now = now or datetime.now(timezone.utc)
    rows = {(m.asset_id, m.variable): m for m in db.execute(select(ForecastModel).where(ForecastModel.tenant_id == tenant_id)).scalars()}
    for asset in assets:
        variable = VARIABLES.get(asset.asset_type)
        if variable is None:
            continue
        row = rows.get((asset.id, variable))
        if row is None:
            break
        age = now - row.trained_at
        if age > timedelta(hours=criteria.retrain_hours) or (row.status != "trained" and age > timedelta(minutes=15)):
            break
    else:
        return False
    train_tenant_models(db, tenant_id, assets, criteria, now)
    return True


def model_summary(row) -> dict:
    return {
        "asset_id": row.asset_id, "variable": row.variable, "algorithm": row.algorithm, "status": row.status,
        "n_samples": row.n_samples, "trained_at": row.trained_at.isoformat() if row.trained_at else None,
        **{k: (row.metrics or {}).get(k) for k in ("mae_ml", "mae_physics", "improvement_pct", "holdout_hours")},
    }
