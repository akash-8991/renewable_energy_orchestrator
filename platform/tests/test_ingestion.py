"""Ingestion validation must fail closed on malformed input without ever
crashing the batch — regression test for the bug where a non-UUID asset_id
raised a DB-level error and aborted the whole ingestion transaction."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion.telemetry_consumer import _validate  # noqa: E402
from reo_common.events import CloudEvent  # noqa: E402


def _event(**data_overrides):
    data = {
        "asset_id": "11111111-1111-1111-1111-111111111111",
        "metric": "power_kw",
        "event_time": "2026-09-24T12:00:00+00:00",
        "value": 100.0,
        "unit": "kW",
        **data_overrides,
    }
    return CloudEvent(type="reo.telemetry.reading", source="test", tenant_id="tenant-1", data=data)


def test_valid_reading_passes():
    ok, reason = _validate(_event())
    assert ok, reason


def test_non_uuid_asset_id_rejected_not_crashed():
    ok, reason = _validate(_event(asset_id="not-a-uuid"))
    assert not ok
    assert "asset_id" in reason


def test_unknown_metric_rejected():
    ok, reason = _validate(_event(metric="made_up_metric"))
    assert not ok
    assert "metric" in reason


def test_non_numeric_value_rejected():
    ok, reason = _validate(_event(value="not-a-number"))
    assert not ok


def test_bad_timestamp_rejected():
    ok, reason = _validate(_event(event_time="not-a-timestamp"))
    assert not ok
