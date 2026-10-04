"""Scheduled re-ingestion of every active data source, with an event-driven
decision trigger.

Every `CONNECTOR_POLL_SECONDS` (default 60) this re-reads each active
`database`, `data_table`, `iot` and `market_energy_purchase` connector
exactly as a manual "Ingest" click would (same code path:
routers/connectors.py's run_connector_ingest). A source whose content is
byte-identical to what it last ingested is skipped; if any source actually
brought in new data, the poller waits for the telemetry consumer to persist
it and then asks the optimizer-worker for an immediate decision cycle
(`trigger="event:data_change"`) instead of leaving the new data to wait for
the next scheduled tick.

Runs as a background thread inside the api process (like folder_watcher and
telemetry_consumer — see docs/SIMPLIFICATIONS.md's modular-monolith note).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from fastapi import HTTPException
from reo_common.config import get_settings
from reo_common.events import EventBus, STREAM_TELEMETRY, request_decision_cycle, stream_head, wait_until_processed
from reo_common.security import AuthContext
from sqlalchemy import select

from database.connection import SessionLocal, break_glass_cross_tenant
from models.canonical import Connector, Tenant

from ..routers.connectors import AUTO_INGEST_STATUS_KEY, POLLABLE_CONNECTOR_KINDS, run_connector_ingest

log = logging.getLogger("api.ingestion.connector_poller")
settings = get_settings()

_stop_event = threading.Event()
TELEMETRY_CONSUMER_GROUP = "api-ingestion"  # telemetry_consumer.run_forever's group
POLLER_LABEL = "connector-poller"


def _record(bus: EventBus, connector_id: str, **fields) -> None:
    """Last-poll outcome per connector, shown in Connector Studio."""
    key = AUTO_INGEST_STATUS_KEY.format(connector_id=connector_id)
    bus._redis.set(key, json.dumps({"polled_at": datetime.now(timezone.utc).isoformat(), **fields}))  # noqa: SLF001


def poll_once(bus: EventBus) -> int:
    """One pass over every active source. Returns the number of rows newly
    ingested across all of them (0 when nothing changed)."""
    total_new_rows = 0
    triggered_tenants: set[str] = set()
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            connectors = db.execute(
                select(Connector).where(Connector.status == "active", Connector.kind.in_(POLLABLE_CONNECTOR_KINDS))
            ).scalars().all()
            for connector in connectors:
                # The connector's own activator is the audit actor (the audit
                # log's actor_id is a user FK); the label says it was automatic.
                ctx = AuthContext(
                    user_id=connector.activated_by or connector.created_by, tenant_id=connector.tenant_id,
                    roles=[], email=POLLER_LABEL,
                )
                try:
                    result = run_connector_ingest(db, ctx, connector, skip_unchanged=True)
                except HTTPException as exc:
                    db.rollback()
                    log.warning("poll of connector %s (%s) failed: %s", connector.name, connector.id, exc.detail)
                    _record(bus, connector.id, outcome="error", rows=0, error=str(exc.detail))
                    continue
                except Exception as exc:
                    db.rollback()
                    log.exception("poll of connector %s (%s) crashed", connector.name, connector.id)
                    _record(bus, connector.id, outcome="error", rows=0, error=str(exc))
                    continue

                if result.rows_queued:
                    log.info("connector %s: ingested %d new rows", connector.name, result.rows_queued)
                    _record(bus, connector.id, outcome="ingested", rows=result.rows_queued)
                    total_new_rows += result.rows_queued
                    triggered_tenants.add(connector.tenant_id)
                else:
                    _record(bus, connector.id, outcome=(result.detail or {}).get("status", "no_new_data"), rows=0)

        # New readings travel through the stream to the database
        # asynchronously; let everything published so far land first so the
        # cycle plans on it.
        if triggered_tenants:
            wait_until_processed(bus, STREAM_TELEMETRY, TELEMETRY_CONSUMER_GROUP, stream_head(bus, STREAM_TELEMETRY))
        for tenant_id in triggered_tenants:
            if _tenant_running(tenant_id):
                request_decision_cycle(bus, tenant_id, "event:data_change", rows=total_new_rows)
                log.info("data changed — requested an on-demand decision cycle for tenant %s", tenant_id)
    finally:
        db.close()
    return total_new_rows


def _tenant_running(tenant_id: str) -> bool:
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalar_one_or_none()
            return tenant is not None and tenant.operating_state == "running"
    finally:
        db.close()


def run_forever() -> None:
    interval = settings.connector_poll_seconds
    if interval <= 0:
        log.info("connector polling disabled (CONNECTOR_POLL_SECONDS=%s)", interval)
        return
    bus = EventBus()
    log.info("connector poller started, interval=%ss", interval)
    while not _stop_event.is_set():
        try:
            poll_once(bus)
        except Exception:
            log.exception("connector poll pass failed, retrying next interval")
        _stop_event.wait(interval)


def start_background_thread() -> threading.Thread:
    thread = threading.Thread(target=run_forever, name="connector-poller", daemon=True)
    thread.start()
    return thread


def stop() -> None:
    _stop_event.set()
