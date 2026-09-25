"""Watched-folder ingestion (FR-DI-002 "path ingestion... checksum/idempotency").

Polls `DATA_WATCH_DIR` (the `data/` folder the user drops files into) every
few seconds rather than relying on filesystem-event notifications — bind
mounts (especially Docker Desktop on macOS) don't reliably deliver inotify
events, and polling a handful of files every few seconds is cheap and
simple. Each file's content checksum is tracked in a Redis set so a file is
only ever ingested once, even if it's re-saved with the same name (content-
addressed, not timestamp-addressed).

Simplification: files dropped here are attributed to the platform's default
tenant (`settings.default_tenant_slug`) — a real multi-tenant deployment
would use a per-tenant subfolder or a tenant-scoped SFTP root instead. The
API upload endpoint (`file_ingest.py` via `routers/ingestion.py`) always
uses the authenticated caller's own tenant, so it doesn't have this
limitation.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from reo_common.config import get_settings
from database.connection import SessionLocal, break_glass_cross_tenant
from reo_common.events import EventBus
from models.canonical import Tenant
from sqlalchemy import select

from .file_ingest import file_checksum, parse_telemetry_file, publish_readings

log = logging.getLogger("api.ingestion.folder_watcher")
settings = get_settings()

_stop_event = threading.Event()
_PROCESSED_SET_KEY = "reo:ingestion:processed_files"
SUPPORTED_SUFFIXES = {".csv", ".json", ".xlsx", ".xlsm"}
POLL_SECONDS = 10


def _default_tenant_id() -> str | None:
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug)).scalar_one_or_none()
            return tenant.id if tenant else None
    finally:
        db.close()


def _scan_once(bus: EventBus, tenant_id: str, watch_dir: Path) -> int:
    if not watch_dir.is_dir():
        return 0
    ingested = 0
    for path in sorted(watch_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        content = path.read_bytes()
        checksum = file_checksum(content)
        if bus._redis.sismember(_PROCESSED_SET_KEY, checksum):  # noqa: SLF001
            continue
        try:
            readings = parse_telemetry_file(path.name, content)
        except Exception:
            log.exception("failed to parse %s — leaving unmarked for retry", path)
            continue
        if readings:
            lineage_id = publish_readings(bus, tenant_id, readings)
            log.info("ingested %d readings from %s (lineage_id=%s)", len(readings), path.name, lineage_id)
            ingested += len(readings)
        bus._redis.sadd(_PROCESSED_SET_KEY, checksum)  # noqa: SLF001
    return ingested


def run_forever() -> None:
    bus = EventBus()
    watch_dir = Path(settings.data_watch_dir)
    log.info("folder watcher started, watching %s", watch_dir)
    tenant_id = None
    while not _stop_event.is_set():
        if tenant_id is None:
            tenant_id = _default_tenant_id()
            if tenant_id is None:
                _stop_event.wait(POLL_SECONDS)
                continue
        try:
            _scan_once(bus, tenant_id, watch_dir)
        except Exception:
            log.exception("folder watcher scan failed, retrying")
        _stop_event.wait(POLL_SECONDS)


def start_background_thread() -> threading.Thread:
    thread = threading.Thread(target=run_forever, name="folder-watcher", daemon=True)
    thread.start()
    return thread


def stop() -> None:
    _stop_event.set()
