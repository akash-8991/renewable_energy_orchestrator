"""Per-tenant circuit breaker + call-timeout wrapper for the model gateway
(production-readiness gap: "no per-agent cost/latency budget or circuit
breaker if the provider is slow/down mid-cycle — a stuck agent call
currently just makes that decision cycle slow").

Two failure modes this closes, both applied around a single provider call:
  - **Timeout**: `call_with_timeout` runs the blocking SDK call in a worker
    thread and raises `TimeoutError` if it exceeds `timeout_seconds`, so a
    hung provider no longer stalls a decision cycle indefinitely.
  - **Circuit breaker**: after `failure_threshold` consecutive failures
    (schema-invalid, timeout, provider error) for a tenant, `allow()` opens
    the circuit for `cooldown_seconds` — later calls fail immediately
    (zero tokens spent, zero latency) instead of retrying a provider that's
    already down.

State lives in Redis, keyed per tenant, mirroring `rate_limit.py`'s own
pattern exactly (fail open on a Redis error — a breaker that can itself
take the platform down when Redis hiccups is worse than an occasional slow
call getting through)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, Protocol, TypeVar


class RedisLike(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ex: int | None = None) -> None: ...
    def incr(self, key: str) -> int: ...
    def expire(self, key: str, seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...


T = TypeVar("T")

# Shared across every breaker instance in this process — a plain function
# call wrapped in a thread, not a persistent connection pool, so there's
# nothing tenant-specific to isolate here.
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="model-call-timeout")


class ModelCallCircuitBreaker:
    def __init__(
        self, redis_client: RedisLike, *, tenant_id: str, timeout_seconds: float,
        failure_threshold: int, cooldown_seconds: int,
    ):
        self._redis = redis_client
        self.tenant_id = tenant_id or "no-tenant"
        self.timeout_seconds = timeout_seconds
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

    def _open_key(self) -> str:
        return f"reo:circuit:model:{self.tenant_id}:open_until"

    def _fail_key(self) -> str:
        return f"reo:circuit:model:{self.tenant_id}:failures"

    def allow(self) -> tuple[bool, str | None]:
        try:
            open_until = self._redis.get(self._open_key())
        except Exception:
            return True, None  # fail open — see module docstring
        if open_until and float(open_until) > time.time():
            remaining = float(open_until) - time.time()
            return False, f"circuit breaker open for tenant={self.tenant_id} — retry in {remaining:.0f}s"
        return True, None

    def record_success(self) -> None:
        try:
            self._redis.delete(self._fail_key())
            self._redis.delete(self._open_key())
        except Exception:
            pass

    def record_failure(self) -> None:
        try:
            count = self._redis.incr(self._fail_key())
            self._redis.expire(self._fail_key(), max(self.cooldown_seconds * 4, 300))
            if count >= self.failure_threshold:
                self._redis.set(self._open_key(), str(time.time() + self.cooldown_seconds), ex=self.cooldown_seconds + 5)
        except Exception:
            pass

    def call_with_timeout(self, fn: Callable[..., T], *args, **kwargs) -> T:
        future = _executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeoutError as exc:
            raise TimeoutError(f"model call exceeded {self.timeout_seconds}s timeout") from exc
