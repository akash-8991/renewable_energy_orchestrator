"""Asset agent (doc 07 §4): assesses capability via telemetry/limits/health/
maintenance/warranty/environment. Returns a capability envelope and
unavailable intervals. Never exceeds OEM/client limits in its own
recommendations."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Asset Agent.

Scope: assess the health and capability of the portfolio's assets (generation, battery, consumer
load groups) from their nameplate data, battery state-of-health/warranty figures, and current
telemetry. Flag any asset whose real-time behaviour looks inconsistent with its nameplate rating
(e.g. reporting well above rated capacity), and flag batteries with low remaining warranty cycles
or state-of-health as a degradation risk the portfolio manager should see.

You never recommend an operating point beyond an asset's rated capacity or its battery's
configured SoC bounds — those are hard limits set by other systems, not yours to relax."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    telemetry_by_asset: dict[str, list[dict]] = {}
    for t in bundle.telemetry:
        telemetry_by_asset.setdefault(t["asset_id"], []).append(t)

    evidence = {
        "assets": bundle.assets,
        "batteries": bundle.batteries,
        "latest_telemetry_by_asset": telemetry_by_asset,
    }
    return run_agent(
        gateway, agent_name="asset", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=["digital_twin", "maintenance_records"],
    )
