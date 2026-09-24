"""Event bus abstraction over Redis Streams (documented substitute for
Kafka/MQTT — TRD §7 "Events"). The envelope follows the CloudEvents shape so
a real Kafka/MQTT adapter could be dropped in later without touching
business logic (only `EventBus` would change).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis

from .config import get_settings

settings = get_settings()


def new_correlation_id(prefix: str = "cyc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class CloudEvent:
    def __init__(
        self,
        *,
        type: str,
        source: str,
        tenant_id: str | None,
        data: dict[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ):
        self.id = str(uuid.uuid4())
        self.type = type
        self.source = source
        self.tenant_id = tenant_id
        self.time = datetime.now(timezone.utc).isoformat()
        self.data = data
        self.correlation_id = correlation_id or new_correlation_id()
        self.causation_id = causation_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "specversion": "1.0",
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "tenant_id": self.tenant_id,
            "time": self.time,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CloudEvent":
        ev = cls(
            type=raw["type"], source=raw["source"], tenant_id=raw.get("tenant_id"),
            data=raw.get("data", {}), correlation_id=raw.get("correlation_id"),
            causation_id=raw.get("causation_id"),
        )
        ev.id = raw.get("id", ev.id)
        ev.time = raw.get("time", ev.time)
        return ev


class EventBus:
    def __init__(self, redis_url: str | None = None):
        # socket_timeout=None: XREADGROUP's own BLOCK argument governs how
        # long a call waits, not the socket — otherwise redis-py's default
        # socket timeout fires first and surfaces as a spurious TimeoutError
        # on every empty poll.
        self._redis = redis.from_url(redis_url or settings.redis_url, decode_responses=True, socket_timeout=None)

    def publish(self, stream: str, event: CloudEvent) -> str:
        return self._redis.xadd(stream, {"payload": json.dumps(event.to_dict())})

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(self, stream: str, group: str, consumer: str, count: int = 10, block_ms: int = 5000):
        self.ensure_group(stream, group)
        resp = self._redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        events = []
        for _stream_name, entries in resp or []:
            for entry_id, fields in entries:
                event = CloudEvent.from_dict(json.loads(fields["payload"]))
                events.append((entry_id, event))
        return events

    def ack(self, stream: str, group: str, entry_id: str) -> None:
        self._redis.xack(stream, group, entry_id)

    def dead_letter(self, stream: str, group: str, entry_id: str, event: CloudEvent, error: str) -> None:
        self._redis.xadd(f"{stream}.dlq", {"payload": json.dumps({**event.to_dict(), "error": error})})
        self.ack(stream, group, entry_id)


# Canonical stream names used across services
STREAM_TELEMETRY = "reo.telemetry"
STREAM_DECISION_CYCLE = "reo.decision.cycle"
STREAM_DECISION_READY = "reo.decision.ready"
STREAM_SIGNAL_STATE = "reo.signal.state"
STREAM_DASHBOARD_FANOUT = "reo.dashboard.fanout"
