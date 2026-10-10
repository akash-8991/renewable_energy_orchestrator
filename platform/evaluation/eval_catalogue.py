"""Plain-data catalogue of the evaluation cases: name, agent, tier and what each one checks.

Kept free of any agent/model imports so the API (which does not ship the agent code) can describe a
stored run — including runs recorded before results carried their own description. The cases themselves
(and how each is run and scored) live in eval_harness.py; tests/test_eval_report.py fails if the two
drift apart.
"""

from __future__ import annotations

TIER_MEANING = {
    "structural": "Wiring check: the agent returns a schema-valid response. True for any working model, including the mock.",
    "behavioral": "Reasoning check: the agent must actually notice something in the evidence (needs a live model; skipped on mock).",
}

CASES: list[dict] = [
    {"name": 'data_quality_schema_valid', "agent": 'data_quality', "tier": 'structural',
     "description": 'data_quality agent returns a schema-valid AgentEnvelope for a normal evidence bundle.'},
    {"name": 'data_quality_flags_majority_stale_telemetry', "agent": 'data_quality', "tier": 'behavioral',
     "description": 'When every reading in the snapshot is stale/bad quality, the agent must raise at least one HIGH/CRITICAL finding rather than staying silent.'},
    {"name": 'risk_critic_schema_valid', "agent": 'risk_critic', "tier": 'structural',
     "description": 'risk_critic agent returns a schema-valid AgentEnvelope for a normal evidence bundle.'},
    {"name": 'risk_critic_flags_single_battery_concentration', "agent": 'risk_critic', "tier": 'behavioral',
     "description": "When every proposed action concentrates on the portfolio's single battery with no fallback, the adversarial risk_critic must raise at least a MEDIUM finding."},
    {"name": 'governance_schema_valid', "agent": 'governance', "tier": 'structural',
     "description": 'governance agent returns a schema-valid GovernanceAssessment for a normal evidence bundle.'},
    {"name": 'governance_requires_approval_when_no_constraint_authorises_action', "agent": 'governance', "tier": 'behavioral',
     "description": "Per the agent's own prompt ('the absence of a rule is not a rule permitting the action'), a high-risk action with zero supporting constraints must require human approval."},
    {"name": 'forecast_schema_valid', "agent": 'forecast', "tier": 'structural',
     "description": 'forecast agent returns a schema-valid AgentEnvelope for a normal evidence bundle.'},
    {"name": 'forecast_flags_asset_with_no_forecast_series', "agent": 'forecast', "tier": 'behavioral',
     "description": "Per the agent's own prompt, a solar asset with zero forecast rows must be flagged at least MEDIUM (the optimizer would have treated it as zero)."},
    {"name": 'asset_schema_valid', "agent": 'asset', "tier": 'structural',
     "description": 'asset agent returns a schema-valid AgentEnvelope for a normal evidence bundle.'},
    {"name": 'asset_flags_telemetry_above_nameplate_rating', "agent": 'asset', "tier": 'behavioral',
     "description": 'A 1,000kW-rated asset reporting 5,000kW must be flagged as inconsistent with its nameplate rating.'},
    {"name": 'market_schema_valid', "agent": 'market', "tier": 'structural',
     "description": "market agent returns a schema-valid AgentEnvelope for a normal evidence bundle (no behavioral case: this agent's evidence is a thin, free-form decision summary with no clean numeric mismatch to assert on)."},
    {"name": 'grid_schema_valid', "agent": 'grid', "tier": 'structural',
     "description": 'grid agent returns a schema-valid AgentEnvelope for a normal evidence bundle.'},
    {"name": 'grid_flags_export_limit_binding_for_many_consecutive_hours', "agent": 'grid', "tier": 'behavioral',
     "description": "Per the agent's own prompt, an export limit binding for 24 consecutive plan steps (no headroom for an unplanned event) is worth at least a LOW finding."},
    {"name": 'optimisation_reviewer_schema_valid', "agent": 'optimisation_reviewer', "tier": 'structural',
     "description": 'optimisation_reviewer returns a schema-valid AgentEnvelope for a normal evidence bundle.'},
    {"name": 'optimisation_reviewer_flags_non_optimal_solver_status', "agent": 'optimisation_reviewer', "tier": 'behavioral',
     "description": "Per the agent's own prompt, a solver_status of INFEASIBLE (not OPTIMAL) must be flagged at least MEDIUM."},
]

BY_NAME = {c["name"]: c for c in CASES}
