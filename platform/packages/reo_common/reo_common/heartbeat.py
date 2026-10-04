"""Worker liveness via Redis heartbeats.

A worker that has hung (deadlocked, stuck on a dead connection) still looks
"running" to Docker/ECS. Each long-running service calls `beat("name")` from
its main loop; the key expires if the loop stops, and the container
healthcheck (`python -m reo_common.heartbeat check name`) fails, so the
orchestrator restarts it."""

from __future__ import annotations

import sys
import time

from .config import get_settings

KEY = "reo:heartbeat:{name}"
TTL_SECONDS = 120
_MIN_INTERVAL = 5.0
_last: dict[str, float] = {}
_client = None


def _redis():
    global _client
    if _client is None:
        import redis

        _client = redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
    return _client


def beat(name: str) -> None:
    """Cheap to call every loop iteration: writes at most once per 5 seconds
    and never raises (a Redis hiccup must not take a worker down)."""
    now = time.monotonic()
    if now - _last.get(name, 0.0) < _MIN_INTERVAL:
        return
    _last[name] = now
    try:
        _redis().set(KEY.format(name=name), str(int(time.time())), ex=TTL_SECONDS)
    except Exception:
        pass


def is_alive(name: str) -> bool:
    try:
        return bool(_redis().exists(KEY.format(name=name)))
    except Exception:
        return False


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        sys.exit(0 if is_alive(sys.argv[2]) else 1)
    print("usage: python -m reo_common.heartbeat check <service-name>")
    sys.exit(2)
