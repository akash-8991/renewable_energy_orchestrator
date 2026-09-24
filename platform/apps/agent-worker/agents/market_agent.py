"""Market agent (doc 07 §4): summarises positions/prices/gate closures/
imbalance/contract limits/DR incentives, mapped to the market time unit.
Never places or advises an unauthorised market order."""

from __future__ import annotations

from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway

from .base import AgentEnvelope, run_agent

SPECIALIST_PROMPT = """You are the Market Agent.

Scope: interpret the forecast market price series and the plan's grid import/export schedule.
Summarise the price trend over the horizon (rising, falling, volatile), and comment on whether the
chosen import/export schedule looks well-timed against that price trend (e.g. exporting during
forecast price troughs would be a finding worth raising, not a directive — the optimizer already
made this decision; you are reviewing its market-sensibility with fresh eyes).

You never place, cancel, or advise on an actual market order or contractual position — you only
comment on the schedule the optimizer already produced. Note explicitly that the platform's
10-minute dispatch cadence is distinct from the market's own settlement/imbalance period; do not
conflate the two in your findings."""


def assess(gateway: ModelGateway, bundle: EvidenceBundle, tenant_id: str, correlation_id: str) -> AgentEnvelope:
    price_points = sorted(
        [f for f in bundle.forecasts if f["variable"] == "price" and f["quantile"] == 0.5],
        key=lambda r: r["valid_time"],
    )
    plan_steps = bundle.decision.get("n_plan_steps", 0)
    evidence = {
        "price_forecast_q50_series": price_points,
        "decision_summary": bundle.decision,
        "note": "the platform's decision cadence (10 min / on-event) is independent of the market settlement period",
    }
    return run_agent(
        gateway, agent_name="market", tenant_id=tenant_id, correlation_id=correlation_id,
        specialist_prompt=SPECIALIST_PROMPT, evidence=evidence,
        tool_allowlist=["market_api", "contract_data"],
    )
