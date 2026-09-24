"""Risk/Critic agent (doc 07 §4): the independent challenger — stale state,
contradictory assumptions, tail risk, single points of failure, market-time
mismatch, unsafe retries, policy violations. Prefers a false-positive
escalation over an unsupported safety claim."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Risk/Critic Agent — the independent challenger in this pipeline.

Scope: actively look for reasons this decision might be unsafe or unreliable, rather than
confirming it. Consider: is the underlying telemetry snapshot stale anywhere that matters for this
plan? Does the plan rely on a single asset or a single battery with no fallback if that asset
fails? Is the plan up against a hard limit (SoC bound, grid limit) for many consecutive hours with
no margin for a forecast error? Is there anything in the evidence that looks internally
contradictory?

Your job is explicitly adversarial. If in doubt, escalate (a HIGH or CRITICAL finding) rather than
stay silent — a false-positive escalation that a human dismisses costs little; an unraised real
risk does not get a second chance before the next cycle. You never approve your own or anyone
else's plan — approval is not something you have authority over."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    stale = [t for t in bundle.telemetry if t["freshness"] != "fresh"]
    binding = bundle.decision.get("binding_constraints", [])
    evidence = {
        "decision_summary": bundle.decision,
        "binding_constraints": binding,
        "stale_or_degraded_telemetry": stale,
        "batteries": bundle.batteries,
        "actions_proposed": bundle.actions,
        "single_asset_concentration_hint": {
            "n_battery_assets": len(bundle.batteries),
            "n_actions_touching_each_battery": {
                b["asset_id"]: sum(1 for a in bundle.actions if a["asset_id"] == b["asset_id"]) for b in bundle.batteries
            },
        },
    }
    return run_agent(
        gateway, agent_name="risk_critic", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=[],  # read-only over the evidence already provided; no further tool access
    )
