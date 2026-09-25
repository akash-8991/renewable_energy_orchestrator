"""Agent Observability & Evaluation workspace — the concrete thing behind
the MODEL_ADMIN role's `manage:model_registry`/`manage:model_eval`/
`deploy:model` permissions, which existed in the RBAC catalogue
(`reo_common/security.py`) with no endpoint or UI behind any of them.

Two distinct concerns, both real, both bounded (see docs/SIMPLIFICATIONS.md
for what a fuller version of each would need):
  - Call-health observability: every `ModelGateway.complete_structured(...)`
    call any agent or the document-intake vision path makes is persisted as
    an `AgentCallLog` row (`reo_common/observability.py`'s hook). This
    surfaces real call volume, latency, schema-validity and retry rate —
    not a mocked-up chart.
  - Evaluation: a small fixed-scenario regression suite
    (`apps/agent-worker/eval_harness.py`) that a portfolio's MODEL_ADMIN can
    trigger on demand. Triggering publishes onto the event bus (the same
    pattern every other cross-service action in this platform uses) since
    the harness's agent code lives in the agent-worker service/container,
    not the api container.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from reo_common.events import CloudEvent, EventBus, STREAM_EVAL_REQUEST
from reo_common.models import AgentCallLog, AgentEvalRun
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/observability", tags=["observability"])

_bus: EventBus | None = None


def _get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


class AgentCallOut(BaseModel):
    id: str
    agent: str
    correlation_id: str
    provider: str
    model: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    schema_valid: bool
    retried: bool
    error: str | None
    created_at: str


class AgentSummaryRow(BaseModel):
    agent: str
    total_calls: int
    schema_valid_calls: int
    retried_calls: int
    avg_latency_ms: float
    p95_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int


class ObservabilitySummary(BaseModel):
    total_calls: int
    schema_valid_rate: float
    retry_rate: float
    by_agent: list[AgentSummaryRow]
    providers_in_use: list[str]


@router.get("/agent-calls", response_model=list[AgentCallOut])
def list_agent_calls(
    agent: str | None = Query(None),
    schema_valid: bool | None = Query(None),
    limit: int = Query(100, le=1000),
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> list[AgentCallOut]:
    stmt = select(AgentCallLog).where(AgentCallLog.tenant_id == ctx.tenant_id)
    if agent:
        stmt = stmt.where(AgentCallLog.agent == agent)
    if schema_valid is not None:
        stmt = stmt.where(AgentCallLog.schema_valid == schema_valid)
    rows = db.execute(stmt.order_by(AgentCallLog.created_at.desc()).limit(limit)).scalars().all()
    return [
        AgentCallOut(
            id=r.id, agent=r.agent, correlation_id=r.correlation_id, provider=r.provider, model=r.model,
            latency_ms=r.latency_ms, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
            schema_valid=r.schema_valid, retried=r.retried, error=r.error, created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/summary", response_model=ObservabilitySummary)
def get_observability_summary(
    since_hours: int = Query(24, le=24 * 30),
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> ObservabilitySummary:
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    rows = db.execute(
        select(AgentCallLog).where(AgentCallLog.tenant_id == ctx.tenant_id, AgentCallLog.created_at >= since)
    ).scalars().all()

    if not rows:
        return ObservabilitySummary(total_calls=0, schema_valid_rate=1.0, retry_rate=0.0, by_agent=[], providers_in_use=[])

    by_agent_raw: dict[str, list[AgentCallLog]] = {}
    for r in rows:
        by_agent_raw.setdefault(r.agent, []).append(r)

    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
        return s[idx]

    by_agent = [
        AgentSummaryRow(
            agent=agent_name,
            total_calls=len(calls),
            schema_valid_calls=sum(1 for c in calls if c.schema_valid),
            retried_calls=sum(1 for c in calls if c.retried),
            avg_latency_ms=round(sum(c.latency_ms for c in calls) / len(calls), 1),
            p95_latency_ms=round(_p95([c.latency_ms for c in calls]), 1),
            total_input_tokens=sum(c.input_tokens or 0 for c in calls),
            total_output_tokens=sum(c.output_tokens or 0 for c in calls),
        )
        for agent_name, calls in sorted(by_agent_raw.items())
    ]

    return ObservabilitySummary(
        total_calls=len(rows),
        schema_valid_rate=round(sum(1 for r in rows if r.schema_valid) / len(rows), 4),
        retry_rate=round(sum(1 for r in rows if r.retried) / len(rows), 4),
        by_agent=by_agent,
        providers_in_use=sorted({r.provider for r in rows}),
    )


class EvalRunOut(BaseModel):
    id: str
    triggered_by: str | None
    model_provider: str
    total_cases: int
    passed_cases: int
    results: list[dict]
    created_at: str


@router.get("/eval-runs", response_model=list[EvalRunOut])
def list_eval_runs(
    limit: int = Query(20, le=100),
    ctx: AuthContext = Depends(require_permission("read:dashboard")),
    db: Session = Depends(db_session),
) -> list[EvalRunOut]:
    rows = db.execute(
        select(AgentEvalRun).where(AgentEvalRun.tenant_id == ctx.tenant_id).order_by(AgentEvalRun.created_at.desc()).limit(limit)
    ).scalars().all()
    return [
        EvalRunOut(id=r.id, triggered_by=r.triggered_by, model_provider=r.model_provider, total_cases=r.total_cases,
                    passed_cases=r.passed_cases, results=r.results, created_at=r.created_at.isoformat())
        for r in rows
    ]


class EvalRunTriggerResponse(BaseModel):
    status: str


@router.post("/eval-runs/run", response_model=EvalRunTriggerResponse)
def trigger_eval_run(
    ctx: AuthContext = Depends(require_permission("manage:model_eval")),
) -> EvalRunTriggerResponse:
    _get_bus().publish(STREAM_EVAL_REQUEST, CloudEvent(
        type="reo.eval.requested", source="api", tenant_id=ctx.tenant_id,
        data={"tenant_id": ctx.tenant_id, "triggered_by": ctx.email},
    ))
    return EvalRunTriggerResponse(status="queued")
