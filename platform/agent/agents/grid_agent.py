"""Grid agent (doc 07 §4): assesses network topology/interconnection/
transmission limits/reserve/frequency/N-1 posture. Identifies hard
constraints and margins. Never changes protection/relay settings."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Grid Agent.

Scope: assess grid-interconnection constraints (import/export limits), the plan's proximity to
those limits (binding constraints), and frequency telemetry as a coarse reliability signal.
Comment on the margin remaining against hard grid limits over the horizon — a plan that binds the
export limit for many consecutive hours is worth flagging even though it is not itself a
violation, since it leaves no headroom for an unplanned event.

You never propose changing a protection relay setting, an interconnection limit, or any other
grid-code parameter — you only assess margin against limits set elsewhere."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    grid_constraints = [c for c in bundle.constraints if c["constraint_type"] == "grid_import_export_limit"]
    frequency_readings = [t for t in bundle.telemetry if t["metric"] == "frequency_hz"]
    evidence = {
        "grid_constraints": grid_constraints,
        "frequency_telemetry": frequency_readings,
        "binding_constraints_in_plan": bundle.decision.get("binding_constraints", []),
    }
    return run_agent(
        gateway, agent_name="grid", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=["topology_service", "constraint_registry", "alarms"],
    )
