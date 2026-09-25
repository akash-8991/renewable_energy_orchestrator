"""Governance agent (doc 07 §4): applies supplied policy facts to classify
risk and required approval. Never interprets ambiguous policy expansively —
no explicit rule permitting an action means block/escalate.

Note: this agent's output is *advisory input* to the deterministic
policy/safety engine (Phase 5), which is the actual authority over
disposition — see FR-GV-001/002. The agent's classification never bypasses
that engine; it is one more piece of evidence the engine (and a human
reviewer) can read.
"""

from __future__ import annotations

from typing import Literal

from context import EvidenceBundle
from pydantic import BaseModel, Field
from reo_common.model_gateway import ModelGateway

from .base import run_agent

SPECIALIST_PROMPT = """You are the Governance Agent.

Scope: given the proposed actions, their risk levels, and the binding constraints they sit against,
classify an overall risk tier for this decision cycle and state whether human approval should be
required before any of these actions could be executed. Cite the specific policy/constraint
evidence your classification rests on.

You never relax a policy or invent a permission that was not explicitly evidenced. If nothing in
the evidence explicitly authorises an action class, treat it as requiring approval — the absence of
a rule is not a rule permitting the action."""


class GovernanceAssessment(BaseModel):
    status: Literal["OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"]
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    requires_human_approval: bool
    policy_basis: list[str] = Field(default_factory=list)
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> GovernanceAssessment:
    evidence = {
        "decision_summary": bundle.decision,
        "actions_proposed": bundle.actions,
        "constraints": bundle.constraints,
    }
    return run_agent(
        gateway, agent_name="governance", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence, response_model=GovernanceAssessment,
        tool_allowlist=["policy_api", "approval_api"],
    )
