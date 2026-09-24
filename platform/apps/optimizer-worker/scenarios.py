"""Scenario engine (FR-SC-001/002/003, PRD E3).

Simplification (docs/SIMPLIFICATIONS.md): a full stochastic MILP over many
Monte Carlo trajectories is out of scope for this build's solve-time budget.
Instead this module (a) names the same shock scenarios the edge-simulator
can inject live (cloud cover, wind surge, price spike, battery outage, line
congestion, demand shock) so the Scenario Lab and the live demo speak the
same vocabulary, and (b) turns each asset's quantile forecast band into one
*risk-adjusted deterministic* series — a well-established surrogate for
full CVaR optimization: plan against `mean + risk_aversion * (q90 - q50)`
instead of the raw mean, so higher `risk_aversion` in the tenant's
ObjectivePolicy produces a more conservative plan without needing a
stochastic solver. The solver module runs this alongside the plain q50
series to produce the "risk-averse" alternative plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

NAMED_SCENARIOS = {
    "CLOUD_COVER": {"solar_multiplier": 0.3},
    "WIND_SURGE": {"wind_multiplier": 1.6},
    "PRICE_SPIKE": {"price_multiplier": 2.2},
    "BATTERY_OUTAGE": {"battery_available": False},
    "LINE_CONGESTION": {"grid_limit_multiplier": 0.3},
    "DEMAND_SHOCK": {"demand_multiplier": 1.5},
}


@dataclass
class ForecastSeries:
    """One variable's forecast over the horizon, per quantile."""
    valid_times: list[datetime]
    q10: list[float]
    q50: list[float]
    q90: list[float]
    unit: str


def build_series(points: list, valid_times_sorted: list[datetime]) -> ForecastSeries:
    """`points` is a list of ForecastPoint (forecast.py) for one asset."""
    by_time_q: dict[tuple[datetime, float], float] = {(p.valid_time, p.quantile): p.value for p in points}
    unit = points[0].unit if points else ""
    return ForecastSeries(
        valid_times=valid_times_sorted,
        q10=[by_time_q.get((t, 0.1), 0.0) for t in valid_times_sorted],
        q50=[by_time_q.get((t, 0.5), 0.0) for t in valid_times_sorted],
        q90=[by_time_q.get((t, 0.9), 0.0) for t in valid_times_sorted],
        unit=unit,
    )


def risk_adjusted_series(series: ForecastSeries, risk_aversion: float, *, downside_is_high: bool) -> list[float]:
    """`downside_is_high=True` for cost-like variables (price, demand) where
    the risky/conservative direction is the upper quantile; False for
    revenue-like/generation variables where the conservative direction is
    the lower quantile (planning for less available generation than expected).
    """
    out = []
    for q10, q50, q90 in zip(series.q10, series.q50, series.q90):
        if downside_is_high:
            adjusted = q50 + risk_aversion * (q90 - q50)
        else:
            adjusted = q50 - risk_aversion * (q50 - q10)
        out.append(max(0.0, adjusted))
    return out


def apply_named_scenario(series: ForecastSeries, scenario_name: str, kind: str) -> list[float]:
    """Apply a named shock multiplier to a series' q50, for what-if
    simulation (Simulation Lab) independent of the live edge-simulator
    injection of the same scenario."""
    shock = NAMED_SCENARIOS.get(scenario_name, {})
    multiplier_key = {"solar": "solar_multiplier", "wind": "wind_multiplier", "price": "price_multiplier", "demand": "demand_multiplier"}.get(kind)
    multiplier = shock.get(multiplier_key, 1.0) if multiplier_key else 1.0
    return [v * multiplier for v in series.q50]
