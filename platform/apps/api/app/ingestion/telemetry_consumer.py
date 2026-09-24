"""Streaming telemetry ingestion (FR-DI-004/005): consumes `reo.telemetry`
(Redis Stream — see SIMPLIFICATIONS.md for why Redis Streams stands in for
Kafka/MQTT here), validates each reading against the asset registry, and
persists it idempotently into the Timescale hypertable. Anything that fails
validation is quarantined to object storage rather than silently dropped —
this is the "landing/quarantine zone" from doc 05 §8.

Runs as a background thread inside the api process (started from
app/main.py's lifespan) rather than a separate container — see
docs/SIMPLIFICATIONS.md's "modular monolith" rationale. redis-py's sync
client is what EventBus wraps, so a plain thread (not an asyncio task) is
the natural fit.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

from reo_common.db import SessionLocal, break_glass_cross_tenant
from reo_common.events import CloudEvent, EventBus, STREAM_TELEMETRY
from reo_common.models import Asset, Telemetry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .quarantine import quarantine_payload

log = logging.getLogger("api.ingestion.telemetry")

_stop_event = threading.Event()
_KNOWN_METRICS = {
    "power_kw", "soc_pct", "temperature_c", "irradiance_w_m2", "wind_speed_ms",
    "market_price_gbp_per_mwh", "frequency_hz", "net_import_kw",
}


def _asset_exists(db, tenant_id: str, asset_id: str) -> bool:
    return db.execute(
        select(Asset.id).where(Asset.tenant_id == tenant_id, Asset.id == asset_id)
    ).scalar_one_or_none() is not None


def _validate(event: CloudEvent) -> tuple[bool, str | None]:
    data = event.data
    if not event.tenant_id:
        return False, "missing tenant_id"
    for field in ("asset_id", "metric", "event_time", "value", "unit"):
        if field not in data:
            return False, f"missing field: {field}"
    # asset_id must be syntactically a UUID *before* it ever reaches a query —
    # a malformed value hitting `Asset.id == asset_id` (a UUID column) raises
    # a DB-level DataError that would abort the whole batch's transaction
    # instead of just quarantining the one bad row.
    try:
        uuid.UUID(str(data["asset_id"]))
    except (ValueError, AttributeError, TypeError):
        return False, f"asset_id is not a valid UUID: {data['asset_id']!r}"
    if data["metric"] not in _KNOWN_METRICS:
        return False, f"unknown metric: {data['metric']}"
    try:
        float(data["value"])
    except (TypeError, ValueError):
        return False, "value is not numeric"
    try:
        datetime.fromisoformat(data["event_time"])
    except ValueError:
        return False, "event_time is not ISO8601"
    return True, None


def _process_batch(bus: EventBus, group: str, consumer: str) -> int:
    events = bus.consume(STREAM_TELEMETRY, group, consumer, count=200, block_ms=5000)
    if not events:
        return 0

    db = SessionLocal()
    processed = 0
    try:
        with break_glass_cross_tenant():
            for entry_id, event in events:
                ok, reason = _validate(event)
                if not ok:
                    quarantine_payload("telemetry", event.to_dict(), reason=reason or "invalid")
                    bus.ack(STREAM_TELEMETRY, group, entry_id)
                    continue

                # Each row gets its own SAVEPOINT: an unexpected DB error on
                # one malformed row (e.g. a constraint we haven't
                # anticipated) must not abort the whole batch's transaction
                # and strand every other, valid row in it.
                try:
                    with db.begin_nested():
                        if not _asset_exists(db, event.tenant_id, event.data["asset_id"]):
                            quarantine_payload("telemetry", event.to_dict(), reason="unknown asset_id")
                            bus.ack(STREAM_TELEMETRY, group, entry_id)
                            continue

                        stmt = pg_insert(Telemetry).values(
                            tenant_id=event.tenant_id,
                            asset_id=event.data["asset_id"],
                            metric=event.data["metric"],
                            event_time=datetime.fromisoformat(event.data["event_time"]),
                            value=float(event.data["value"]),
                            unit=event.data["unit"],
                            quality=event.data.get("quality", "good"),
                            source=event.data.get("source", "edge-simulator"),
                        ).on_conflict_do_nothing(constraint="uq_telemetry_reading")
                        db.execute(stmt)
                except Exception:
                    log.exception("failed to persist one telemetry reading, quarantining and continuing")
                    quarantine_payload("telemetry", event.to_dict(), reason="persist_error")
                else:
                    processed += 1
                bus.ack(STREAM_TELEMETRY, group, entry_id)
            db.commit()
    except Exception:
        db.rollback()
        log.exception("telemetry batch failed, will be redelivered to the consumer group")
        raise
    finally:
        db.close()
    return processed


def run_forever(consumer_name: str = "api-ingestion-1") -> None:
    bus = EventBus()
    group = "api-ingestion"
    bus.ensure_group(STREAM_TELEMETRY, group)
    log.info("telemetry ingestion consumer started (group=%s consumer=%s)", group, consumer_name)
    while not _stop_event.is_set():
        try:
            n = _process_batch(bus, group, consumer_name)
            if n:
                log.debug("persisted %d telemetry readings", n)
        except Exception:
            log.exception("telemetry consumer loop error, retrying")
            _stop_event.wait(2)


def start_background_thread() -> threading.Thread:
    thread = threading.Thread(target=run_forever, name="telemetry-ingestion", daemon=True)
    thread.start()
    return thread


def stop() -> None:
    _stop_event.set()
