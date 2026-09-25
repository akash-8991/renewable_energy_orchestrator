"""Applies document-derived capacity constraints to a generation asset's
per-step forecast series before the optimizer sees it — this is what makes
an uploaded maintenance notice/storm alert (`apps/api/app/ingestion/
document_ingest.py`, hackathon problem 4 solution depth D3) a real input to
the decision cycle rather than just something a human can read in the UI.

A `Constraint` row with constraint_type="capacity_derate_from_document"
caps how much of an asset's rated capacity is available during its
effective window (e.g. a turbine offline for inspection). Extracted into
its own module, separate from `cycle.py`'s DB-bound orchestration, so the
derate arithmetic is unit-testable with plain datetimes and floats.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TypedDict


class ActiveDerate(TypedDict):
    max_capacity_pct: float
    effective_from: datetime
    effective_to: datetime


def apply_capacity_derates(
    forecast_kw: list[float],
    *,
    rated_capacity_kw: float,
    now: datetime,
    step_hours: float,
    derates: list[ActiveDerate],
) -> list[float]:
    if not derates:
        return forecast_kw
    derated = list(forecast_kw)
    for t in range(len(derated)):
        step_time = now + timedelta(hours=(t + 1) * step_hours)
        cap_pct: float | None = None
        for d in derates:
            if d["effective_from"] <= step_time <= d["effective_to"]:
                cap_pct = d["max_capacity_pct"] if cap_pct is None else min(cap_pct, d["max_capacity_pct"])
        if cap_pct is not None:
            derated[t] = min(derated[t], rated_capacity_kw * cap_pct / 100.0)
    return derated
