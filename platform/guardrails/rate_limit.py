"""Token-conservation rate limiting for the model gateway — a per-tenant
Redis-backed counter checked *before* any provider call is made, so a call
that would exceed budget spends zero tokens rather than being throttled
after the fact. Two windows (per-minute burst, per-day budget) because a
burst cap alone doesn't stop a misbehaving loop from quietly running up a
large bill over hours; a low per-minute cap alone would make a legitimate
multi-agent decision cycle (9 agents in quick succession) trip constantly.

Deliberately not a general-purpose HTTP rate limiter — FastAPI middleware,
etc. This only gates `ModelGateway.complete_structured(...)`, where the
actual cost (LLM tokens) is incurred.
"""

from __future__ import annotations

import time
from typing import Protocol


class RedisLike(Protocol):
    def pipeline(self) -> "PipelineLike": ...


class PipelineLike(Protocol):
    def incr(self, key: str) -> "PipelineLike": ...
    def expire(self, key: str, seconds: int) -> "PipelineLike": ...
    def execute(self) -> list: ...


class ModelCallRateLimiter:
    """`allow(...)` increments both windows unconditionally (so the caller
    always knows the current count even on a block) and returns whether the
    call may proceed. Fails open on a Redis error — a rate limiter that can
    itself take the whole platform down when Redis hiccups is worse than an
    occasionally-oversized bill."""

    def __init__(self, redis_client: RedisLike, *, per_minute: int, per_day: int):
        self._redis = redis_client
        self.per_minute = per_minute
        self.per_day = per_day

    def allow(self, *, agent: str, tenant_id: str) -> tuple[bool, str | None]:
        now = int(time.time())
        scope = tenant_id or "no-tenant"
        minute_key = f"reo:ratelimit:model:{scope}:min:{now // 60}"
        day_key = f"reo:ratelimit:model:{scope}:day:{now // 86400}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(minute_key)
            pipe.expire(minute_key, 90)
            pipe.incr(day_key)
            pipe.expire(day_key, 90_000)
            minute_count, _, day_count, _ = pipe.execute()
        except Exception:
            return True, None  # fail open — see class docstring

        if minute_count > self.per_minute:
            return False, f"per-minute model-call budget exceeded ({minute_count}/{self.per_minute}) for tenant={scope}, agent={agent}"
        if day_count > self.per_day:
            return False, f"per-day model-call budget exceeded ({day_count}/{self.per_day}) for tenant={scope}, agent={agent}"
        return True, None
