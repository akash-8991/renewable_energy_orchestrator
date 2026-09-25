"""A document-derived capacity derate must only bite the forecast steps it
actually covers, clip to (not below) whatever the forecast already says,
and stack correctly when more than one derate overlaps the same asset."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "optimizer-worker"))

from document_constraints import apply_capacity_derates  # noqa: E402

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_no_derates_returns_forecast_unchanged():
    forecast = [100.0, 200.0, 300.0]
    out = apply_capacity_derates(forecast, rated_capacity_kw=500.0, now=NOW, step_hours=1.0, derates=[])
    assert out == forecast
    assert out is forecast  # no defensive copy needed when there's nothing to change


def test_derate_only_applies_within_its_window():
    # steps land at NOW+1h, +2h, +3h, +4h; window covers only step 2 (+2h)
    forecast = [100.0, 100.0, 100.0, 100.0]
    derates = [{
        "max_capacity_pct": 20.0,
        "effective_from": NOW + timedelta(hours=1, minutes=30),
        "effective_to": NOW + timedelta(hours=2, minutes=30),
    }]
    out = apply_capacity_derates(forecast, rated_capacity_kw=500.0, now=NOW, step_hours=1.0, derates=derates)
    assert out == [100.0, 100.0, 100.0, 100.0]
    # step index 1 is the +2h step, the only one inside the window
    out2 = apply_capacity_derates(forecast, rated_capacity_kw=500.0, now=NOW, step_hours=1.0, derates=derates)
    assert out2[1] == 100.0  # 500*20% = 100, forecast already below that cap -> unaffected
    assert out2[0] == 100.0 and out2[2] == 100.0 and out2[3] == 100.0


def test_derate_clips_forecast_down_to_the_capacity_cap():
    forecast = [400.0, 400.0]
    derates = [{"max_capacity_pct": 25.0, "effective_from": NOW, "effective_to": NOW + timedelta(hours=3)}]
    out = apply_capacity_derates(forecast, rated_capacity_kw=1000.0, now=NOW, step_hours=1.0, derates=derates)
    # 1000 * 25% = 250 kW cap, forecast of 400 kW gets clipped down to it
    assert out == [250.0, 250.0]


def test_derate_never_raises_forecast_above_original():
    # a generous "derate" (90%) on a step where forecast was already low
    # (e.g. solar at dusk) must not bump generation up
    forecast = [10.0]
    derates = [{"max_capacity_pct": 90.0, "effective_from": NOW, "effective_to": NOW + timedelta(hours=2)}]
    out = apply_capacity_derates(forecast, rated_capacity_kw=1000.0, now=NOW, step_hours=1.0, derates=derates)
    assert out == [10.0]


def test_overlapping_derates_take_the_most_restrictive():
    forecast = [500.0]
    derates = [
        {"max_capacity_pct": 60.0, "effective_from": NOW, "effective_to": NOW + timedelta(hours=2)},
        {"max_capacity_pct": 20.0, "effective_from": NOW, "effective_to": NOW + timedelta(hours=2)},
    ]
    out = apply_capacity_derates(forecast, rated_capacity_kw=1000.0, now=NOW, step_hours=1.0, derates=derates)
    assert out == [200.0]  # 1000 * 20%, not 60%


def test_fully_unavailable_zeroes_the_step():
    forecast = [500.0]
    derates = [{"max_capacity_pct": 0.0, "effective_from": NOW, "effective_to": NOW + timedelta(hours=2)}]
    out = apply_capacity_derates(forecast, rated_capacity_kw=1000.0, now=NOW, step_hours=1.0, derates=derates)
    assert out == [0.0]
