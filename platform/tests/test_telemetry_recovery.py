"""A telemetry batch that was in flight when the api restarted is delivered
to the consumer but never acknowledged. The consumer only asks for *new*
entries, so those readings used to sit in the group's pending list forever;
on startup it now replays its own unacknowledged entries first."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.ingestion import telemetry_consumer  # noqa: E402
from reo_common.events import CloudEvent, EventBus  # noqa: E402


def test_unacknowledged_entries_are_replayed_and_acked_on_startup(monkeypatch):
    bus = EventBus()
    stream, group, consumer = f"reo.test.{uuid.uuid4().hex[:8]}", "g", "c1"
    monkeypatch.setattr(telemetry_consumer, "STREAM_TELEMETRY", stream)
    seen: list[dict] = []

    def fake_process(bus_, group_, events):
        for entry_id, event in events:
            seen.append(event.data)
            bus_.ack(stream, group_, entry_id)
        return len(events)

    monkeypatch.setattr(telemetry_consumer, "_process_events", fake_process)
    try:
        bus.ensure_group(stream, group)
        for i in range(3):
            bus.publish(stream, CloudEvent(type="t", source="test", tenant_id="x", data={"i": i}))
        assert len(bus.consume(stream, group, consumer, count=10, block_ms=100)) == 3  # delivered, never acked
        assert bus._redis.xpending(stream, group)["pending"] == 3

        assert telemetry_consumer._recover_pending(bus, group, consumer) == 3

        assert sorted(d["i"] for d in seen) == [0, 1, 2]
        assert bus._redis.xpending(stream, group)["pending"] == 0
        assert telemetry_consumer._recover_pending(bus, group, consumer) == 0  # nothing left to replay
    finally:
        bus._redis.delete(stream)
