"""Explanation agent (doc 07 §4-5): generates the operator-facing
explanation strictly from decision evidence — what changed, the selected
plan, why, objectives gained/sacrificed, binding constraints, uncertainty,
alternatives rejected, required approval, expected result. No unsupported
causal claims: every explanation must be traceable to the evidence bundle.
"""

from __future__ import annotations

from context import EvidenceBundle
from pydantic import BaseModel, Field
from reo_common.model_gateway import ModelGateway

from .base import run_agent

SPECIALIST_PROMPT = """You are the Explanation Agent.

Scope: write a concise, operator-facing explanation of this decision cycle using ONLY the fixed
template fields below, grounded strictly in the evidence provided — the plan, its binding
constraints, its confidence, and the risk-averse alternative the optimizer also computed. Do not
invent a causal story ("because cloud cover reduced solar output") unless that specific claim is
actually supported by the telemetry/forecast evidence you were given; if you cannot support a
"why", say what changed and what was decided without asserting an unsupported cause.

Keep it readable for a control-room operator, not a data scientist: short sentences, concrete
numbers where you have them, no jargon that isn't explained."""


class OperatorExplanation(BaseModel):
    decision_id: str
    mode: str
    situation: str = Field(description="what changed / observed state, grounded in evidence")
    selected_plan: str = Field(description="one or two sentences summarising the chosen actions")
    reason: str = Field(description="objective + constraint summary for why this plan was chosen")
    trade_offs: str = Field(description="what was gained/sacrificed vs the risk-averse alternative")
    binding_constraints: list[str] = Field(default_factory=list)
    uncertainty: str
    alternative_considered: str
    governance_basis: str
    execution_status: str
    expiry_or_fallback: str


def explain(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> OperatorExplanation:
    evidence = {
        "decision_summary": bundle.decision,
        "actions_proposed": bundle.actions,
        "objective_policy": bundle.objective_policy,
    }
    return run_agent(
        gateway, agent_name="explanation", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence, response_model=OperatorExplanation,
        tool_allowlist=[],
    )
