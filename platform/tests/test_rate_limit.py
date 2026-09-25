"""Model-call rate limiting must block *before* any token is spent, must
fail open if Redis itself is unavailable (a limiter that can take the
platform down is worse than an oversized bill), and must be wired through
ModelGateway.complete_structured so a blocked call never reaches
`_raw_call` at all."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "reo_common"))

from reo_common.model_gateway import ModelGateway, RateLimitExceeded, RawCallResult  # noqa: E402
from reo_common.rate_limit import ModelCallRateLimiter  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class _Result(BaseModel):
    status: str


class _CountingGateway(ModelGateway):
    provider_name = "test"
    model_name = "test-model"

    def __init__(self):
        self.calls = 0

    def _raw_call(self, *, system_prompt, user_content, json_schema, schema_name, images=None) -> RawCallResult:
        self.calls += 1
        return RawCallResult(raw_json='{"status": "OK"}')


class _FakePipeline:
    def __init__(self, store: dict):
        self._store = store
        self._ops: list[tuple[str, str, int | None]] = []

    def incr(self, key):
        self._ops.append(("incr", key, None))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    def execute(self):
        results = []
        for op, key, _ in self._ops:
            if op == "incr":
                self._store[key] = self._store.get(key, 0) + 1
                results.append(self._store[key])
            else:
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def pipeline(self):
        return _FakePipeline(self.store)


class _BrokenRedis:
    def pipeline(self):
        raise ConnectionError("redis is down")


def test_limiter_allows_calls_under_budget():
    limiter = ModelCallRateLimiter(_FakeRedis(), per_minute=5, per_day=100)
    for _ in range(5):
        allowed, reason = limiter.allow(agent="test", tenant_id="tenant-1")
        assert allowed, reason


def test_limiter_blocks_once_per_minute_budget_exceeded():
    limiter = ModelCallRateLimiter(_FakeRedis(), per_minute=3, per_day=100)
    outcomes = [limiter.allow(agent="test", tenant_id="tenant-1")[0] for _ in range(5)]
    assert outcomes == [True, True, True, False, False]


def test_limiter_tracks_tenants_independently():
    redis = _FakeRedis()
    limiter = ModelCallRateLimiter(redis, per_minute=1, per_day=100)
    assert limiter.allow(agent="a", tenant_id="tenant-1")[0] is True
    assert limiter.allow(agent="a", tenant_id="tenant-1")[0] is False
    # a different tenant has its own untouched budget
    assert limiter.allow(agent="a", tenant_id="tenant-2")[0] is True


def test_limiter_fails_open_when_redis_is_unreachable():
    limiter = ModelCallRateLimiter(_BrokenRedis(), per_minute=1, per_day=100)
    allowed, reason = limiter.allow(agent="test", tenant_id="tenant-1")
    assert allowed is True
    assert reason is None


def test_blocked_call_never_reaches_the_provider():
    gateway = _CountingGateway()
    gateway.rate_limiter = ModelCallRateLimiter(_FakeRedis(), per_minute=1, per_day=100)

    gateway.complete_structured(
        agent="a", tenant_id="tenant-1", correlation_id="c1",
        system_prompt="sp", user_content="uc", response_model=_Result,
    )
    assert gateway.calls == 1

    with pytest.raises(RateLimitExceeded):
        gateway.complete_structured(
            agent="a", tenant_id="tenant-1", correlation_id="c2",
            system_prompt="sp", user_content="uc", response_model=_Result,
        )
    # the second call was blocked before _raw_call ran — no tokens spent
    assert gateway.calls == 1


def test_rate_limit_exceeded_is_a_gateway_error_subclass():
    from reo_common.model_gateway import GatewayError

    assert issubclass(RateLimitExceeded, GatewayError)
