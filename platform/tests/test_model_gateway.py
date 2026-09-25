"""Guardrail: the mock gateway (used whenever no API key is configured) must
still return schema-valid, typed responses — the pipeline should never
silently pass through free-form text. Also covers complete_structured's
fail-closed contract for a real (non-mock) provider whose call itself fails
— not just one that returns invalid JSON."""

import pytest
from pydantic import BaseModel

from reo_common.model_gateway import GatewayError, ModelGateway, MockModelGateway, RawCallResult


class _Finding(BaseModel):
    finding: str
    severity: str
    confidence: float


class _AgentEnvelope(BaseModel):
    status: str
    findings: list[_Finding]
    pii_detected: bool


def test_mock_gateway_returns_schema_valid_envelope():
    gateway = MockModelGateway()
    result, record = gateway.complete_structured(
        agent="test-agent",
        tenant_id="tenant-1",
        correlation_id="cyc-1",
        system_prompt="You are a test agent.",
        user_content="Evaluate the situation.",
        response_model=_AgentEnvelope,
    )
    assert isinstance(result, _AgentEnvelope)
    assert record.schema_valid is True
    assert record.provider == "mock"


class _ExplodingGateway(ModelGateway):
    """A gateway whose _raw_call always raises a plain provider-level
    exception — standing in for what an unhandled openai.APIStatusError
    (billing/rate-limit/auth/network) actually looks like from
    complete_structured's point of view: some exception type that is not
    ValidationError/JSONDecodeError/ValueError."""

    provider_name = "exploding"

    def __init__(self):
        self.calls = 0

    def _raw_call(self, **kwargs) -> RawCallResult:
        self.calls += 1
        raise RuntimeError("simulated provider failure (e.g. HTTP 402/429/5xx)")


def test_provider_level_failure_fails_closed_not_uncaught():
    """Regression test: a raw provider-call exception (not a schema/parsing
    failure) must surface as GatewayError, the same as a schema-invalid
    response does — never escape complete_structured uncaught. Before this
    fix, a real OpenRouter 402 (insufficient credits) propagated straight
    through as openai.APIStatusError, which agent/worker.py's per-agent
    `except GatewayError` never catches — crashing the entire decision's
    agent pass before Decision.reasoning was ever committed, which is
    exactly why Operator Explanation/Agent Findings stayed empty."""
    gateway = _ExplodingGateway()
    with pytest.raises(GatewayError):
        gateway.complete_structured(
            agent="test-agent",
            tenant_id="tenant-1",
            correlation_id="cyc-1",
            system_prompt="You are a test agent.",
            user_content="Evaluate the situation.",
            response_model=_AgentEnvelope,
        )
    # retries once (2 attempts total) before failing closed, same as a
    # schema-invalid response would.
    assert gateway.calls == 2
