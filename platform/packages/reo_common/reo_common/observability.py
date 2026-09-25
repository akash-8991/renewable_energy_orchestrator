"""Agent observability persistence (MODEL_ADMIN's own workspace — see
`AgentCallLog`/`AgentEvalRun` in models.py for why these tables exist).

`ModelGateway.complete_structured(...)` already builds a `ModelCallRecord`
for every call it makes, success or fail-closed failure, and invokes an
optional `on_call_record` hook with it (`model_gateway.py`). This module is
that hook's implementation: turn the in-memory record into a persisted row.
Kept out of `model_gateway.py` itself so that module stays DB-agnostic and
trivially usable in the many tests that construct a bare `MockModelGateway()`
with no DB session at all.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .model_gateway import ModelCallRecord
from .models import AgentCallLog


def persist_call_record(db: Session, record: ModelCallRecord) -> AgentCallLog:
    row = AgentCallLog(
        tenant_id=record.tenant_id,
        agent=record.agent,
        correlation_id=record.correlation_id,
        provider=record.provider,
        model=record.model,
        latency_ms=record.latency_ms,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        schema_valid=record.schema_valid,
        retried=record.retried,
        error=record.error,
    )
    db.add(row)
    return row
