"""Data-quality agent (doc 07 §4): assesses freshness/completeness/units/
timestamp/duplicates/outliers/contradictions/source quality of the trusted
snapshot; identifies unsafe variables. Never repairs the source silently —
it only recommends quarantine/interpolation/fallback per policy."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Data-Quality Agent.

Scope: assess the freshness, completeness, unit consistency, timestamp sanity, duplication,
outlier behaviour and source quality of the telemetry snapshot provided to you. Identify which
readings (if any) are unsafe to plan against.

You may recommend quarantine, interpolation, or falling back to a baseline — but ONLY as a
recommendation in `requested_next_steps`; you never repair, interpolate, or silently correct the
source data yourself. If more than a small fraction of readings are stale or of bad quality, say
so plainly in a HIGH or CRITICAL finding — the platform's autonomy mode should be reduced when
data quality is poor, and that judgement depends on your assessment being honest, not reassuring."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    stale = [t for t in bundle.telemetry if t["freshness"] != "fresh"]
    evidence = {
        "telemetry_snapshot": bundle.telemetry,
        "summary": {
            "total_readings": len(bundle.telemetry),
            "stale_or_bad_readings": len(stale),
            "distinct_assets_reporting": len({t["asset_id"] for t in bundle.telemetry}),
            "distinct_metrics": sorted({t["metric"] for t in bundle.telemetry}),
        },
    }
    return run_agent(
        gateway, agent_name="data_quality", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=["data_catalogue", "quality_api"],
    )
