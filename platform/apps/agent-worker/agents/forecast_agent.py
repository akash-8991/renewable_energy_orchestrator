"""Forecast agent (doc 07 §4): compares the approved forecast candidate
against the baseline, reports point/quantile spread, error/drift where
known, and suitability. Never selects an unapproved model; if the active
model is degraded, recommends falling back and flags higher uncertainty."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Forecast Agent.

Scope: review the forecast bundle (solar, wind, demand, price) that the optimizer used for this
decision cycle. Report on quantile spread (q10/q50/q90) as an uncertainty signal, whether any
series is flagged as a fallback/baseline forecast rather than a trained model, and whether the
spread looks unusually wide (a possible sign of a volatile period the operator should know about).

You never select or deploy a different forecast model yourself — that is a Model Admin action.
If a forecast series is missing entirely for an asset that should have one, that is at least a
MEDIUM severity finding, since the optimizer would have had to treat it as zero."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    by_variable: dict[str, list[dict]] = {}
    for f in bundle.forecasts:
        by_variable.setdefault(f["variable"], []).append(f)

    summary = {}
    for variable, rows in by_variable.items():
        q50_vals = [r["value"] for r in rows if r["quantile"] == 0.5]
        q10_vals = [r["value"] for r in rows if r["quantile"] == 0.1]
        q90_vals = [r["value"] for r in rows if r["quantile"] == 0.9]
        summary[variable] = {
            "n_points": len(q50_vals),
            "q50_min": min(q50_vals) if q50_vals else None,
            "q50_max": max(q50_vals) if q50_vals else None,
            "mean_spread_q90_minus_q10": (sum(a - b for a, b in zip(q90_vals, q10_vals)) / len(q90_vals)) if q90_vals and q10_vals else None,
            "any_fallback": any(r["is_fallback"] for r in rows),
            "model_versions": sorted({r["model_version"] for r in rows}),
        }

    evidence = {"forecast_summary_by_variable": summary, "assets_expected_to_forecast": [a for a in bundle.assets if a["asset_type"] in ("solar", "wind", "consumer", "grid_interconnection")]}
    return run_agent(
        gateway, agent_name="forecast", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=["model_registry", "forecast_api"],
    )
