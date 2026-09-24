"""Digital twin state estimation (FR-DT-002: "state estimation w/ freshness/
quality/confidence"). Shared between the API's read endpoints and the
data-quality agent (doc 07 §4) so both apply the same staleness rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Telemetry

# A reading older than this is "stale but usable with reduced confidence";
# older than STALE_BAD_SECONDS and it can no longer back a decision (FRD §3.2
# degraded-mode rule: "stale telemetry -> freeze/reduce autonomy").
FRESH_SECONDS = 30
STALE_BAD_SECONDS = 300


@dataclass
class LatestReading:
    asset_id: str
    metric: str
    value: float
    unit: str
    event_time: datetime
    quality: str
    age_seconds: float
    freshness: str  # fresh | stale | bad
    confidence: float


def assess_freshness(event_time: datetime, quality: str) -> tuple[str, float, float]:
    now = datetime.now(timezone.utc)
    age = (now - event_time).total_seconds()
    if quality == "bad" or age > STALE_BAD_SECONDS:
        return "bad", age, 0.1
    if quality == "estimated" or age > FRESH_SECONDS:
        return "stale", age, 0.6
    return "fresh", age, 0.98


def latest_readings_for_tenant(db: Session, tenant_id: str, asset_ids: list[str] | None = None) -> list[LatestReading]:
    """Latest value per (asset_id, metric) using a window function — one
    query regardless of how many assets/metrics exist."""
    ranked = (
        select(
            Telemetry.asset_id, Telemetry.metric, Telemetry.value, Telemetry.unit,
            Telemetry.event_time, Telemetry.quality,
            func.row_number().over(
                partition_by=(Telemetry.asset_id, Telemetry.metric),
                order_by=Telemetry.event_time.desc(),
            ).label("rn"),
        )
        .where(Telemetry.tenant_id == tenant_id)
    )
    if asset_ids:
        ranked = ranked.where(Telemetry.asset_id.in_(asset_ids))
    subq = ranked.subquery()
    stmt = select(subq).where(subq.c.rn == 1)

    results = []
    for row in db.execute(stmt).all():
        freshness, age, confidence = assess_freshness(row.event_time, row.quality)
        results.append(
            LatestReading(
                asset_id=row.asset_id, metric=row.metric, value=row.value, unit=row.unit,
                event_time=row.event_time, quality=row.quality, age_seconds=age,
                freshness=freshness, confidence=confidence,
            )
        )
    return results
