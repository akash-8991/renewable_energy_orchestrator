"""Agent evaluation runs: they must not be broken by — or break — live decision
passes, must not pile up when clicked repeatedly, and must not score the
model's unavailability as the agent's failure. Also: Claude sometimes wraps an
otherwise-correct structured answer in a stray `$PARAMETER_NAME` key, which used
to fail the schema check (and, with it, two evaluation cases)."""

import sys
import uuid
from pathlib import Path

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "evaluation"))
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import redis  # noqa: E402
from agents.base import AgentEnvelope  # noqa: E402
from app.routers import observability as obs  # noqa: E402
from eval_harness import run_eval_suite  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from guardrails.rate_limit import ModelCallRateLimiter  # noqa: E402
from reo_common.config import get_settings  # noqa: E402
from reo_common.events import EVAL_INFLIGHT_KEY  # noqa: E402
from reo_common.model_gateway import (  # noqa: E402
    CircuitOpen, GatewayError, ModelGateway, ProviderUnavailable, RateLimitExceeded, RawCallResult, unwrap_tool_input,
)
from reo_common.security import AuthContext  # noqa: E402


class _Gateway(ModelGateway):
    """Test gateway: `behaviour` returns raw JSON, or raises."""

    provider_name = "test"
    model_name = "test-model"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def _raw_call(self, **kwargs) -> RawCallResult:
        return RawCallResult(raw_json=self.behaviour(), input_tokens=1, output_tokens=1)


class _Out(BaseModel):
    status: str
    note: str = ""


def _call(gateway):
    return gateway.complete_structured(
        agent="a", tenant_id="t", correlation_id="c", system_prompt="s", user_content="u", response_model=_Out,
    )


SCHEMA = {"properties": {"status": {}, "note": {}}}


# --- the stray wrapper key ----------------------------------------------------------


def test_a_stray_wrapper_key_is_unwrapped():
    assert unwrap_tool_input({"$PARAMETER_NAME": {"status": "OK"}}, SCHEMA) == {"status": "OK"}
    assert unwrap_tool_input({"input": {"status": "OK", "note": "x"}}, SCHEMA) == {"status": "OK", "note": "x"}


def test_a_correct_payload_is_left_alone():
    ok = {"status": "OK"}
    assert unwrap_tool_input(ok, SCHEMA) is ok
    assert unwrap_tool_input({"status": {"nested": 1}}, SCHEMA) == {"status": {"nested": 1}}  # the one key IS a schema property
    assert unwrap_tool_input({"wrapper": {"unrelated": 1}}, SCHEMA) == {"wrapper": {"unrelated": 1}}  # inner has no schema properties
    assert unwrap_tool_input({"a": {"status": "OK"}, "b": 1}, SCHEMA) == {"a": {"status": "OK"}, "b": 1}  # more than one key


def test_the_gateway_accepts_a_wrapped_answer_end_to_end():
    parsed, record = _call(_Gateway(lambda: '{"$PARAMETER_NAME": {"status": "OK", "note": "fine"}}'))
    assert parsed.status == "OK" and record.schema_valid and not record.retried


def test_a_genuinely_invalid_answer_still_fails_closed():
    with pytest.raises(GatewayError) as exc:
        _call(_Gateway(lambda: '{"nope": 1}'))
    assert not isinstance(exc.value, ProviderUnavailable)


# --- typed failures --------------------------------------------------------------------


def test_a_provider_failure_is_distinguished_from_a_bad_answer():
    def boom():
        raise RuntimeError("Error code: 402 - credit balance too low")

    with pytest.raises(ProviderUnavailable):
        _call(_Gateway(boom))


def test_an_open_breaker_and_an_exhausted_budget_are_typed():
    class _Breaker:
        def allow(self):
            return False, "circuit breaker open — retry in 60s"

    class _Limiter:
        def allow(self, **kw):
            return False, "per-minute model-call budget exceeded"

    breaker_gw = _Gateway(lambda: "{}")
    breaker_gw.circuit_breaker = _Breaker()
    with pytest.raises(CircuitOpen):
        _call(breaker_gw)

    limited_gw = _Gateway(lambda: "{}")
    limited_gw.rate_limiter = _Limiter()
    with pytest.raises(RateLimitExceeded):
        _call(limited_gw)


# --- unavailability is not scored as a failure ----------------------------------------------


def test_cases_run_while_the_model_is_unavailable_are_skipped_not_failed():
    def boom():
        raise RuntimeError("Error code: 529 overloaded")

    summary = run_eval_suite(_Gateway(boom))
    outcomes = {r["outcome"] for r in summary["results"]}
    assert outcomes == {"skipped"}
    assert summary["total_cases"] == 0 and summary["passed_cases"] == 0
    assert all("not scored" in r["detail"] for r in summary["results"])


def test_a_real_wrong_answer_is_still_a_failure():
    summary = run_eval_suite(_Gateway(lambda: '{"status": "OK"}'))  # valid envelope, but behavioural cases expect findings
    assert summary["total_cases"] > 0
    assert any(r["outcome"] == "failed" for r in summary["results"])


# --- separate call budget -----------------------------------------------------------------------


def test_eval_calls_have_their_own_budget():
    client = redis.from_url(get_settings().redis_url, decode_responses=True)
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    production = ModelCallRateLimiter(client, per_minute=2, per_day=1000)
    evaluation = ModelCallRateLimiter(client, per_minute=2, per_day=1000, scope_prefix="eval:")
    try:
        assert production.allow(agent="a", tenant_id=tenant)[0] and production.allow(agent="a", tenant_id=tenant)[0]
        assert production.allow(agent="a", tenant_id=tenant)[0] is False  # production budget used up...
        assert evaluation.allow(agent="a", tenant_id=tenant)[0] is True  # ...evaluation is unaffected
    finally:
        for key in client.scan_iter(f"reo:ratelimit:model:*{tenant}:*"):
            client.delete(key)


def test_the_eval_gateway_is_isolated_from_the_production_breaker_and_budget(monkeypatch):
    import importlib.util

    # load agent/worker.py by path — `worker` alone is ambiguous (policy/worker.py exists too)
    spec = importlib.util.spec_from_file_location("agent_worker_under_test", Path(__file__).parent.parent / "agent" / "worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    production_gateway = _Gateway(lambda: "{}")
    production_gateway.circuit_breaker = object()
    production_gateway.rate_limiter = ModelCallRateLimiter(object(), per_minute=20, per_day=2000)
    monkeypatch.setattr(worker, "get_model_gateway", lambda: production_gateway)

    gateway = worker._eval_gateway()

    assert gateway.circuit_breaker is None
    assert gateway.rate_limiter.scope_prefix == "eval:"
    assert gateway.rate_limiter.per_minute == get_settings().eval_rate_limit_per_minute


# --- one run at a time ------------------------------------------------------------------------------


class _Bus:
    def __init__(self):
        self._redis = redis.from_url(get_settings().redis_url, decode_responses=True)
        self.published = []

    def publish(self, stream, event):  # never reaches the real stream (a running worker would start a real eval)
        self.published.append(stream)
        return "1-0"


def test_a_second_click_while_a_run_is_in_flight_is_refused(monkeypatch):
    bus = _Bus()
    monkeypatch.setattr(obs, "_get_bus", lambda: bus)
    tenant = str(uuid.uuid4())
    ctx = AuthContext(user_id="u", tenant_id=tenant, roles=["model_admin"], email="m@test.example")
    key = EVAL_INFLIGHT_KEY.format(tenant_id=tenant)
    try:
        assert obs.eval_run_status(ctx=ctx).running is False
        assert obs.trigger_eval_run(ctx=ctx).status == "queued"
        assert obs.eval_run_status(ctx=ctx).running is True and obs.eval_run_status(ctx=ctx).started_at

        with pytest.raises(HTTPException) as exc:
            obs.trigger_eval_run(ctx=ctx)
        assert exc.value.status_code == 409 and "already" in exc.value.detail
        assert len(bus.published) == 1  # the duplicate queued nothing

        bus._redis.delete(key)  # what the worker does when the run ends
        assert obs.trigger_eval_run(ctx=ctx).status == "queued"
    finally:
        bus._redis.delete(key)


def test_runs_for_different_tenants_do_not_block_each_other(monkeypatch):
    bus = _Bus()
    monkeypatch.setattr(obs, "_get_bus", lambda: bus)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        for t in (a, b):
            assert obs.trigger_eval_run(ctx=AuthContext(user_id="u", tenant_id=t, roles=[], email="m@test.example")).status == "queued"
    finally:
        for t in (a, b):
            bus._redis.delete(EVAL_INFLIGHT_KEY.format(tenant_id=t))
