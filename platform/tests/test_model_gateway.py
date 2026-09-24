"""Guardrail: the mock gateway (used whenever no API key is configured) must
still return schema-valid, typed responses — the pipeline should never
silently pass through free-form text."""

from pydantic import BaseModel

from reo_common.model_gateway import MockModelGateway


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
