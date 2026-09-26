"""The model-gateway circuit breaker must: open after N consecutive
failures, block calls immediately while open (no provider call, no
latency), reset on success, fail open if Redis itself is unavailable (same
policy as the rate limiter), and enforce a per-call timeout so a hung
provider can't stall a decision cycle indefinitely."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "reo_common"))

from reo_common.model_gateway import GatewayError, ModelGateway, RawCallResult  # noqa: E402
from guardrails.circuit_breaker import ModelCallCircuitBreaker  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class _Result(BaseModel):
    status: str


class _CountingGateway(ModelGateway):
    provider_name = "test"
    model_name = "test-model"

    def __init__(self, *, raise_error: bool = False, sleep_seconds: float = 0.0):
        self.calls = 0
        self._raise_error = raise_error
        self._sleep_seconds = sleep_seconds

    def _raw_call(self, *, system_prompt, user_content, json_schema, schema_name, images=None) -> RawCallResult:
        self.calls += 1
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        if self._raise_error:
            raise RuntimeError("simulated provider failure")
        return RawCallResult(raw_json='{"status": "OK"}')


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def expire(self, key, seconds):
        pass

    def delete(self, key):
        self.store.pop(key, None)


class _BrokenRedis:
    def get(self, key):
        raise ConnectionError("redis is down")

    def set(self, key, value, ex=None):
        raise ConnectionError("redis is down")

    def incr(self, key):
        raise ConnectionError("redis is down")

    def expire(self, key, seconds):
        raise ConnectionError("redis is down")

    def delete(self, key):
        raise ConnectionError("redis is down")


def _breaker(redis=None, **overrides) -> ModelCallCircuitBreaker:
    kwargs = dict(tenant_id="tenant-1", timeout_seconds=5.0, failure_threshold=3, cooldown_seconds=60)
    kwargs.update(overrides)
    return ModelCallCircuitBreaker(redis or _FakeRedis(), **kwargs)


def test_allows_calls_while_closed():
    breaker = _breaker()
    allowed, reason = breaker.allow()
    assert allowed and reason is None


def test_opens_after_failure_threshold_reached():
    breaker = _breaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()
    allowed, reason = breaker.allow()
    assert allowed is False
    assert "circuit breaker open" in reason


def test_stays_closed_below_failure_threshold():
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    allowed, _ = breaker.allow()
    assert allowed is True


def test_success_resets_failure_count():
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    # only 2 consecutive failures since the reset — still under threshold
    allowed, _ = breaker.allow()
    assert allowed is True


def test_tenants_are_isolated():
    redis = _FakeRedis()
    for _ in range(3):
        _breaker(redis, tenant_id="tenant-1", failure_threshold=3).record_failure()
    assert _breaker(redis, tenant_id="tenant-1").allow()[0] is False
    assert _breaker(redis, tenant_id="tenant-2").allow()[0] is True


def test_fails_open_when_redis_is_unreachable():
    breaker = _breaker(_BrokenRedis())
    allowed, reason = breaker.allow()
    assert allowed is True and reason is None
    breaker.record_failure()  # must not raise
    breaker.record_success()  # must not raise


def test_call_with_timeout_raises_on_slow_call():
    breaker = _breaker(timeout_seconds=0.2)
    with pytest.raises(TimeoutError):
        breaker.call_with_timeout(time.sleep, 2)


def test_call_with_timeout_passes_through_fast_call():
    breaker = _breaker(timeout_seconds=5.0)
    assert breaker.call_with_timeout(lambda x: x * 2, 21) == 42


def test_open_circuit_blocks_before_reaching_the_provider():
    gateway = _CountingGateway()
    gateway.circuit_breaker = _breaker(failure_threshold=1)
    gateway.circuit_breaker.record_failure()  # trips the breaker before any call

    with pytest.raises(GatewayError):
        gateway.complete_structured(
            agent="a", tenant_id="tenant-1", correlation_id="c1",
            system_prompt="sp", user_content="uc", response_model=_Result,
        )
    assert gateway.calls == 0


def test_slow_provider_call_times_out_and_fails_closed():
    gateway = _CountingGateway(sleep_seconds=1.0)
    gateway.circuit_breaker = _breaker(timeout_seconds=0.1, failure_threshold=5)

    with pytest.raises(GatewayError):
        gateway.complete_structured(
            agent="a", tenant_id="tenant-1", correlation_id="c1",
            system_prompt="sp", user_content="uc", response_model=_Result,
        )


def test_repeated_failures_trip_the_breaker_through_the_gateway():
    gateway = _CountingGateway(raise_error=True)
    gateway.circuit_breaker = _breaker(failure_threshold=2)

    for _ in range(2):
        with pytest.raises(GatewayError):
            gateway.complete_structured(
                agent="a", tenant_id="tenant-1", correlation_id="c1",
                system_prompt="sp", user_content="uc", response_model=_Result,
            )
    calls_before = gateway.calls

    # the breaker should now be open — a third attempt is blocked before _raw_call
    with pytest.raises(GatewayError):
        gateway.complete_structured(
            agent="a", tenant_id="tenant-1", correlation_id="c2",
            system_prompt="sp", user_content="uc", response_model=_Result,
        )
    assert gateway.calls == calls_before


def test_success_clears_the_breaker_through_the_gateway():
    gateway = _CountingGateway()
    gateway.circuit_breaker = _breaker(failure_threshold=3)

    parsed, record = gateway.complete_structured(
        agent="a", tenant_id="tenant-1", correlation_id="c1",
        system_prompt="sp", user_content="uc", response_model=_Result,
    )
    assert parsed.status == "OK"
    assert record.schema_valid is True
