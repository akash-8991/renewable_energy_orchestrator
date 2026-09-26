"""File/path ingestion (FR-DI-001/002, PR-001): CSV/JSON/XLSX telemetry
files, uploaded via the API or dropped into the watched `data/` folder,
are parsed into the same reading shape the edge simulator publishes and
pushed through the identical `reo.telemetry` stream + consumer path — one
validation/quarantine code path regardless of how a reading arrived.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from pathlib import Path

import openpyxl
from reo_common.events import STREAM_TELEMETRY, CloudEvent, EventBus, new_correlation_id

log = logging.getLogger("api.ingestion.file")

REQUIRED_COLUMNS = {"asset_id", "metric", "event_time", "value", "unit"}


def file_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _rows_from_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _rows_from_json(content: bytes) -> list[dict]:
    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("readings", data.get("data", [data]))
    return data


def _rows_from_xlsx(content: bytes) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
    rows = []
    for raw_row in rows_iter:
        if raw_row is None or all(v is None for v in raw_row):
            continue
        rows.append({header[i]: raw_row[i] for i in range(len(header)) if i < len(raw_row)})
    return rows


def rows_from_file(filename: str, content: bytes) -> list[dict]:
    """Raw rows (untyped, unmapped) for any supported file type. Used by
    parse_telemetry_file() below for the generic asset_id/metric/event_time/
    value/unit shape, and by hackathon_dataset.py's reference-table ingestion
    for the reference dataset's own, different, per-file column shapes."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return _rows_from_csv(content)
    if suffix == ".json":
        return _rows_from_json(content)
    if suffix in (".xlsx", ".xlsm"):
        return _rows_from_xlsx(content)
    raise ValueError(f"unsupported file type: {suffix} (supported: .csv, .json, .xlsx)")


def parse_telemetry_file(filename: str, content: bytes) -> list[dict]:
    rows = rows_from_file(filename, content)
    normalised = []
    for row in rows:
        row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
        missing = REQUIRED_COLUMNS - set(row.keys())
        if missing:
            log.warning("skipping row missing columns %s: %s", missing, row)
            continue
        normalised.append(
            {
                "asset_id": str(row["asset_id"]),
                "metric": str(row["metric"]),
                "event_time": str(row["event_time"]),
                "value": row["value"],
                "unit": str(row["unit"]),
                "quality": str(row.get("quality", "good")),
                "source": str(row.get("source", f"file:{filename}")),
            }
        )
    return normalised


def publish_readings(bus: EventBus, tenant_id: str, readings: list[dict], lineage_id: str | None = None) -> str:
    lineage_id = lineage_id or new_correlation_id("ing")
    for reading in readings:
        event = CloudEvent(
            type="reo.telemetry.reading", source="file-ingestion", tenant_id=tenant_id,
            data=reading, correlation_id=lineage_id,
        )
        bus.publish(STREAM_TELEMETRY, event)
    return lineage_id
