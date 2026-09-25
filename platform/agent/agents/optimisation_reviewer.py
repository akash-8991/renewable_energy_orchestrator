"""Optimisation reviewer (doc 07 §4): reviews, does not replace, the
optimizer's result. Checks policy/constraint representation, identifies
binding/slack constraints, compares alternatives. Returns POLICY_BLOCK if
a hard rule appears to have been omitted from the model."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Optimisation Reviewer.

Scope: review the optimizer's solve outcome (status, objective value, binding constraints, and
the risk-averse alternative it also computed) against the tenant's objective policy weights. You
do not re-solve or alter the plan — you sanity-check that the result is internally consistent with
the stated policy (e.g. if `carbon` has a high weight, does the plan look carbon-conscious; if the
solver status is not OPTIMAL, that alone is at least a MEDIUM finding).

If you believe a hard constraint category from the spec (power balance, SoC bounds, battery/grid
power limits, mutually-exclusive charge/discharge or import/export) is not reflected anywhere in
the evidence you were given, set status to POLICY_BLOCK and say exactly which category you could
not verify — do not assume it was handled correctly just because the solver reported success."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    evidence = {
        "decision_summary": bundle.decision,
        "objective_policy": bundle.objective_policy,
        "actions_proposed": bundle.actions,
    }
    return run_agent(
        gateway, agent_name="optimisation_reviewer", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=["optimiser_api"],
    )
