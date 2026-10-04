"""Scheduled re-ingestion of active data sources + the event-driven decision
trigger (backend/app/ingestion/connector_poller.py).

Two guarantees matter:
  1. an unchanged source is NOT re-ingested on every poll (the reference-
     dataset path stamps rows with fresh timestamps, so a repeat would
     append a duplicate series) — the content gate;
  2. a source that did bring in new data requests an immediate decision
     cycle, once the tenant is actually running, and a failing source
     neither triggers a cycle nor stops the others from being polled."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from fastapi import HTTPException

from app.ingestion import connector_poller  # noqa: E402
from app.routers import connectors as connectors_router  # noqa: E402
from app.routers.connectors import ConnectorIngestResponse, _ContentGate, _Unchanged  # noqa: E402
from database.connection import break_glass_cross_tenant  # noqa: E402
from models.canonical import Connector  # noqa: E402
from reo_common.events import EventBus  # noqa: E402


# --- content gate ---------------------------------------------------------


@pytest.fixture()
def connector_id():
    cid = str(uuid.uuid4())
    yield cid
    connectors_router._fingerprint_store().delete(f"reo:connector:fingerprint:{cid}")


def test_unchanged_content_is_skipped_once_committed(connector_id):
    first = _ContentGate(connector_id, skip_unchanged=True)
    first.check(b"a,b\n1,2\n")
    first.commit()

    with pytest.raises(_Unchanged):
        _ContentGate(connector_id, skip_unchanged=True).check(b"a,b\n1,2\n")


def test_changed_content_is_not_skipped(connector_id):
    gate = _ContentGate(connector_id, skip_unchanged=True)
    gate.check(b"a,b\n1,2\n")
    gate.commit()

    _ContentGate(connector_id, skip_unchanged=True).check(b"a,b\n1,2\n3,4\n")  # must not raise


def test_a_failed_ingest_is_retried_because_nothing_was_committed(connector_id):
    _ContentGate(connector_id, skip_unchanged=True).check(b"payload")  # fetched, ingest then failed -> no commit()

    _ContentGate(connector_id, skip_unchanged=True).check(b"payload")  # same content is still ingested next poll


def test_manual_ingest_never_skips(connector_id):
    seed = _ContentGate(connector_id, skip_unchanged=False)
    seed.check(b"payload")
    seed.commit()

    _ContentGate(connector_id, skip_unchanged=False).check(b"payload")  # an explicit "Ingest" click always runs


def test_manual_ingest_counts_so_the_poller_does_not_repeat_it(connector_id):
    manual = _ContentGate(connector_id, skip_unchanged=False)
    manual.check(b"payload")
    manual.commit()

    with pytest.raises(_Unchanged):
        _ContentGate(connector_id, skip_unchanged=True).check(b"payload")


def _r(asset: str, metric: str, ts: str, value: float = 1.0) -> dict:
    return {"asset_id": asset, "metric": metric, "event_time": ts, "value": value, "unit": "kW"}


@pytest.fixture()
def mark_cleanup(connector_id):
    yield
    connectors_router._fingerprint_store().delete(f"reo:connector:watermark:{connector_id}")


def test_only_readings_newer_than_the_last_ingest_are_taken(connector_id, mark_cleanup):
    first = _ContentGate(connector_id, skip_unchanged=True)
    assert len(first.only_new([_r("a", "power_kw", "2026-10-04T10:00:00+00:00"), _r("a", "power_kw", "2026-10-04T10:05:00+00:00")])) == 2
    first.commit()

    # the file was appended to: the two old rows are stale, only the new one is ingested
    second = _ContentGate(connector_id, skip_unchanged=True)
    fresh = second.only_new([
        _r("a", "power_kw", "2026-10-04T10:00:00+00:00"), _r("a", "power_kw", "2026-10-04T10:05:00+00:00"),
        _r("a", "power_kw", "2026-10-04T10:10:00+00:00", 7.0),
    ])
    assert [x["event_time"] for x in fresh] == ["2026-10-04T10:10:00+00:00"]


def test_a_source_with_nothing_newer_is_treated_as_no_new_data(connector_id, mark_cleanup):
    rows = [_r("a", "power_kw", "2026-10-04T10:00:00+00:00")]
    first = _ContentGate(connector_id, skip_unchanged=True)
    first.only_new(rows)
    first.commit()

    with pytest.raises(_Unchanged):  # e.g. the file was re-saved/re-ordered but carries no later reading
        _ContentGate(connector_id, skip_unchanged=True).only_new(list(reversed(rows)))


def test_marks_are_per_asset_and_metric(connector_id, mark_cleanup):
    first = _ContentGate(connector_id, skip_unchanged=True)
    first.only_new([_r("a", "power_kw", "2026-10-04T10:10:00+00:00")])
    first.commit()

    # an older timestamp for a *different* asset/metric is still new to that series
    fresh = _ContentGate(connector_id, skip_unchanged=True).only_new([
        _r("a", "power_kw", "2026-10-04T10:05:00+00:00"),  # stale for (a, power_kw)
        _r("b", "power_kw", "2026-10-04T10:05:00+00:00"),  # first reading ever for b
        _r("a", "soc_pct", "2026-10-04T10:05:00+00:00"),  # first reading ever for (a, soc_pct)
    ])
    assert sorted((x["asset_id"], x["metric"]) for x in fresh) == [("a", "soc_pct"), ("b", "power_kw")]


def test_manual_ingest_takes_everything_but_still_sets_the_marks(connector_id, mark_cleanup):
    rows = [_r("a", "power_kw", "2026-10-04T10:00:00+00:00")]
    manual = _ContentGate(connector_id, skip_unchanged=False)
    assert manual.only_new(rows) == rows
    manual.commit()
    assert manual.only_new(rows) == rows  # a manual click always re-ingests, even what was already marked

    with pytest.raises(_Unchanged):
        _ContentGate(connector_id, skip_unchanged=True).only_new(rows)  # but the poller won't repeat it


# --- poll pass -------------------------------------------------------------


@pytest.fixture()
def poll_env(db_session, two_tenants, monkeypatch):
    """poll_once opens its own sessions; point them at the test session (whose
    uncommitted fixtures it can then see) and capture instead of publish."""
    t1, _ = two_tenants
    triggers: list[tuple[str, str]] = []
    monkeypatch.setattr(connector_poller, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(connector_poller, "wait_until_processed", lambda *a, **k: True)
    monkeypatch.setattr(
        connector_poller, "request_decision_cycle",
        lambda bus, tenant_id, trigger, **data: triggers.append((tenant_id, trigger)),
    )
    return t1, triggers


def _only_for(connector: Connector, response: ConnectorIngestResponse):
    """Fake run_connector_ingest answering `response` for `connector` and
    "unchanged" for any other active connector already in the database (the
    suite runs against a DB that may hold real ones)."""
    unchanged = ConnectorIngestResponse(rows_queued=0, detail={"status": "no_new_data"})

    def fake(db, ctx, c, *a, **k):
        return response if c.id == connector.id else unchanged

    return fake


def _active_connector(db_session, tenant_id: str, kind: str = "data_table") -> Connector:
    with break_glass_cross_tenant():
        c = Connector(tenant_id=tenant_id, name=f"poll-{uuid.uuid4().hex[:6]}", kind=kind, endpoint_url="x.csv", status="active")
        db_session.add(c)
        db_session.flush()
    return c


def test_new_rows_trigger_a_decision_cycle_for_a_running_tenant(db_session, poll_env, monkeypatch):
    tenant, triggers = poll_env
    tenant.operating_state = "running"
    c = _active_connector(db_session, tenant.id)
    monkeypatch.setattr(connector_poller, "run_connector_ingest", _only_for(c, ConnectorIngestResponse(rows_queued=12)))

    new_rows = connector_poller.poll_once(EventBus())

    assert new_rows == 12
    assert triggers == [(tenant.id, "event:data_change")]


def test_unchanged_source_does_not_trigger(db_session, poll_env, monkeypatch):
    tenant, triggers = poll_env
    tenant.operating_state = "running"
    _active_connector(db_session, tenant.id)
    monkeypatch.setattr(
        connector_poller, "run_connector_ingest",
        lambda *a, **k: ConnectorIngestResponse(rows_queued=0, detail={"status": "no_new_data"}),
    )

    assert connector_poller.poll_once(EventBus()) == 0
    assert triggers == []


def test_idle_tenant_is_not_triggered(db_session, poll_env, monkeypatch):
    tenant, triggers = poll_env
    tenant.operating_state = "idle"  # e.g. only an iot/market connector brought data; those never auto-start
    c = _active_connector(db_session, tenant.id, kind="iot")
    monkeypatch.setattr(connector_poller, "run_connector_ingest", _only_for(c, ConnectorIngestResponse(rows_queued=5)))

    connector_poller.poll_once(EventBus())

    assert triggers == []


def test_a_failing_source_is_recorded_and_does_not_trigger(db_session, poll_env, monkeypatch):
    tenant, triggers = poll_env
    tenant.operating_state = "running"
    bad = _active_connector(db_session, tenant.id)
    bad_id = bad.id

    unchanged = ConnectorIngestResponse(rows_queued=0, detail={"status": "no_new_data"})

    def fake_ingest(db, ctx, c, *a, **k):
        if c.id != bad_id:
            return unchanged
        raise HTTPException(502, "could not fetch")

    monkeypatch.setattr(connector_poller, "run_connector_ingest", fake_ingest)
    status_key = f"reo:connector:poll:{bad_id}"
    try:
        assert connector_poller.poll_once(EventBus()) == 0  # no exception escapes the pass

        recorded = json.loads(connectors_router._fingerprint_store().get(status_key))
        assert recorded["outcome"] == "error"
        assert "could not fetch" in recorded["error"]
        assert triggers == []
    finally:
        connectors_router._fingerprint_store().delete(status_key)


# --- stream catch-up wait ---------------------------------------------------


def test_wait_until_processed_returns_once_entries_up_to_the_cutoff_are_acked():
    from reo_common.events import CloudEvent, stream_head, wait_until_processed

    bus = EventBus()
    stream, group = f"reo.test.{uuid.uuid4().hex[:8]}", "g"
    try:
        bus.ensure_group(stream, group)
        for i in range(3):
            bus.publish(stream, CloudEvent(type="t", source="test", tenant_id=None, data={"i": i}))
        cutoff = stream_head(bus, stream)
        bus.publish(stream, CloudEvent(type="t", source="test", tenant_id=None, data={"i": "after-cutoff"}))

        assert wait_until_processed(bus, stream, group, cutoff, timeout_seconds=0.6) is False  # nothing consumed yet

        entries = bus.consume(stream, group, "c1", count=10, block_ms=100)
        for entry_id, _event in entries[:3]:  # ack the three at/before the cutoff, leave the later one pending
            bus.ack(stream, group, entry_id)

        assert wait_until_processed(bus, stream, group, cutoff, timeout_seconds=2) is True
    finally:
        bus._redis.delete(stream)


def test_wait_until_processed_with_nothing_to_wait_for_returns_immediately():
    from reo_common.events import wait_until_processed

    assert wait_until_processed(EventBus(), "reo.test.none", "g", None) is True
