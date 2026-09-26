"""Agent worker: on every `reo.decision.ready` event, runs the specialist
agent roster (doc 07) against that decision's evidence bundle and persists
their findings into Decision.reasoning / Decision.risk_flags.

Orchestration itself ("Orchestrator" in doc 07 §3 — "build the minimum
safe workflow for the cycle") is this loop's own code, not a further LLM
call: the workflow is already fixed by the deterministic decision-cycle
pipeline (ingest -> forecast -> optimise -> [this]), so there is no
meaningful "which tool to call next" decision left for an LLM to make. Every
specialist agent below still calls the model gateway for its own analysis.
"""

import logging
import time

from agents.asset_agent import assess as assess_asset
from agents.base import AgentEnvelope, GatewayError
from agents.data_quality import assess as assess_data_quality
from agents.explanation_agent import explain as run_explanation
from agents.forecast_agent import assess as assess_forecast
from agents.governance_agent import assess as assess_governance
from agents.grid_agent import assess as assess_grid
from agents.market_agent import assess as assess_market
from agents.optimisation_reviewer import assess as assess_optimisation
from agents.risk_critic import assess as assess_risk
from context import build_evidence_bundle
from evaluation.eval_harness import run_eval_suite
from database.connection import SessionLocal, break_glass_cross_tenant
from reo_common.events import CloudEvent, EventBus, STREAM_DASHBOARD_FANOUT, STREAM_DECISION_READY, STREAM_EVAL_REQUEST
from reo_common.model_gateway import get_model_gateway
from reo_common.platform_settings import attach_circuit_breaker
from models.canonical import AgentEvalRun, Decision
from evaluation.observability import persist_call_record
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s agent-worker %(message)s")
log = logging.getLogger("agent-worker")

SPECIALIST_AGENTS = {
    "data_quality": assess_data_quality,
    "forecast": assess_forecast,
    "asset": assess_asset,
    "market": assess_market,
    "grid": assess_grid,
    "optimisation_reviewer": assess_optimisation,
    "risk_critic": assess_risk,
}


def _envelope_to_dict(name: str, result) -> dict:
    if hasattr(result, "model_dump"):
        return {"agent": name, **result.model_dump()}
    return {"agent": name, "status": "ERROR", "error": str(result)}


def run_agents_for_decision(decision_id: str) -> None:
    gateway = get_model_gateway()
    db = SessionLocal()
    gateway.on_call_record = lambda record: persist_call_record(db, record)
    try:
        with break_glass_cross_tenant():
            decision = db.execute(select(Decision).where(Decision.id == decision_id)).scalar_one_or_none()
            if decision is None:
                log.warning("decision %s not found, skipping agent pass", decision_id)
                return
            tenant_id = decision.tenant_id
            correlation_id = decision.decision_cycle_id
            attach_circuit_breaker(gateway, db, tenant_id)

            bundle = build_evidence_bundle(db, tenant_id, decision)

            reasoning: dict = {"agent_findings": [], "governance": None, "explanation": None}
            new_risk_flags: list[str] = list(decision.risk_flags or [])

            for name, fn in SPECIALIST_AGENTS.items():
                try:
                    result: AgentEnvelope = fn(gateway, bundle, tenant_id, correlation_id)
                    reasoning["agent_findings"].append(_envelope_to_dict(name, result))
                    for finding in result.findings:
                        if finding.severity in ("HIGH", "CRITICAL"):
                            new_risk_flags.append(f"[{name}] {finding.finding}")
                except GatewayError as exc:
                    log.warning("agent %s failed closed for decision %s: %s", name, decision_id, exc)
                    reasoning["agent_findings"].append({"agent": name, "status": "INSUFFICIENT_EVIDENCE", "error": str(exc)})
                    new_risk_flags.append(f"[{name}] agent response was schema-invalid after retry — treated as insufficient evidence")

            try:
                governance = assess_governance(gateway, bundle, tenant_id, correlation_id)
                reasoning["governance"] = governance.model_dump()
                if governance.requires_human_approval:
                    decision.status = "proposed"  # unchanged, but explicit: governance concurs approval is needed
            except GatewayError as exc:
                log.warning("governance agent failed closed for decision %s: %s", decision_id, exc)
                reasoning["governance"] = {"status": "INSUFFICIENT_EVIDENCE", "error": str(exc)}
                new_risk_flags.append("[governance] agent response was schema-invalid after retry — treat as approval-required by default")

            try:
                explanation = run_explanation(gateway, bundle, tenant_id, correlation_id)
                reasoning["explanation"] = explanation.model_dump()
            except GatewayError as exc:
                log.warning("explanation agent failed closed for decision %s: %s", decision_id, exc)
                reasoning["explanation"] = {"status": "INSUFFICIENT_EVIDENCE", "error": str(exc)}

            decision.reasoning = reasoning
            decision.risk_flags = new_risk_flags
            db.commit()

            bus = EventBus()
            bus.publish(STREAM_DASHBOARD_FANOUT, CloudEvent(
                type="reo.decision.explained", source="agent-worker", tenant_id=tenant_id,
                data={"decision_id": decision_id, "risk_flag_count": len(new_risk_flags)},
                correlation_id=correlation_id,
            ))
            log.info("agent pass complete for decision %s: %d specialist findings, %d risk flags", decision_id, len(reasoning["agent_findings"]), len(new_risk_flags))
    except Exception:
        db.rollback()
        log.exception("agent pass failed for decision %s", decision_id)
        raise
    finally:
        db.close()


def run_eval_request(tenant_id: str | None, triggered_by: str | None) -> None:
    gateway = get_model_gateway()
    db = SessionLocal()
    gateway.on_call_record = lambda record: persist_call_record(db, record)
    try:
        with break_glass_cross_tenant():
            if tenant_id:
                attach_circuit_breaker(gateway, db, tenant_id)
            summary = run_eval_suite(gateway, tenant_id=tenant_id) if tenant_id else run_eval_suite(gateway)
            db.add(AgentEvalRun(
                tenant_id=tenant_id, triggered_by=triggered_by, model_provider=summary["model_provider"],
                total_cases=summary["total_cases"], passed_cases=summary["passed_cases"], results=summary["results"],
            ))
            db.commit()
            log.info("eval suite run complete: %d/%d cases passed (provider=%s)", summary["passed_cases"], summary["total_cases"], summary["model_provider"])
    except Exception:
        db.rollback()
        log.exception("eval suite run failed")
        raise
    finally:
        db.close()


def main() -> None:
    gateway = get_model_gateway()
    log.info("agent-worker started, model gateway provider=%s model=%s", gateway.provider_name, getattr(gateway, "model_name", "n/a"))
    bus = EventBus()
    bus.ensure_group(STREAM_DECISION_READY, "agent-worker")
    bus.ensure_group(STREAM_EVAL_REQUEST, "agent-worker")
    while True:
        events = bus.consume(STREAM_DECISION_READY, "agent-worker", "worker-1", block_ms=3000)
        for entry_id, event in events:
            decision_id = event.data.get("decision_id")
            if decision_id:
                try:
                    run_agents_for_decision(decision_id)
                except Exception:
                    log.exception("failed to process decision %s, will not retry automatically", decision_id)
            bus.ack(STREAM_DECISION_READY, "agent-worker", entry_id)

        eval_events = bus.consume(STREAM_EVAL_REQUEST, "agent-worker", "worker-1", block_ms=500)
        for entry_id, event in eval_events:
            try:
                run_eval_request(event.data.get("tenant_id"), event.data.get("triggered_by"))
            except Exception:
                log.exception("failed to process eval request")
            bus.ack(STREAM_EVAL_REQUEST, "agent-worker", entry_id)

        if not events and not eval_events:
            time.sleep(1)


if __name__ == "__main__":
    main()
