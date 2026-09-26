"""Fixed-scenario evaluation harness for the specialist agents — the
concrete thing behind the `manage:model_eval` permission (MODEL_ADMIN role),
which existed with no endpoint or UI behind it until this.

Two tiers of check, run against synthetic `EvidenceBundle` fixtures built
in-process (no DB, no live decision cycle needed):

- STRUCTURAL: true for any schema-valid response, including the
  zero-reasoning `MockModelGateway` — these catch wiring regressions (an
  agent crashing, a schema field renamed, a prompt that stops parsing)
  regardless of which provider is configured.
- BEHAVIORAL: requires the model to have actually reasoned about the
  evidence (e.g. "flags a HIGH/CRITICAL finding when battery SoC is
  critically low and told to be adversarial"). `MockModelGateway` fills
  only schema-required fields with generic placeholders and can never pass
  these by construction, so they're reported SKIPPED rather than FAILED
  when the active gateway is mock — a wrong 0%-pass reading would look like
  a real regression instead of the expected demo-mode limitation.

This is deliberately small (15 cases across 7 of the 9 specialist agents —
governance_agent and explanation_agent, plus data_quality/risk_critic, are
covered; forecast/asset/market/grid/optimisation_reviewer were added in a
later pass), not a comprehensive eval suite — see docs/SIMPLIFICATIONS.md
for what a fuller one (a real labelled corpus drawn from actual usage,
human/rubric scoring, statistical significance over repeated runs) would
need beyond what a coding session can produce without real usage data to
draw one from. market_agent has a structural case only, not a behavioral
one — its evidence is a thin, free-form decision summary with no clean
numeric mismatch to assert on, and inventing one would be a guessed check,
not a genuinely detectable signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from agents.asset_agent import assess as assess_asset
from agents.base import AgentEnvelope, GatewayError
from agents.data_quality import assess as assess_data_quality
from agents.forecast_agent import assess as assess_forecast
from agents.governance_agent import GovernanceAssessment
from agents.governance_agent import assess as assess_governance
from agents.grid_agent import assess as assess_grid
from agents.market_agent import assess as assess_market
from agents.optimisation_reviewer import assess as assess_optimisation_reviewer
from agents.risk_critic import assess as assess_risk_critic
from context import EvidenceBundle
from reo_common.model_gateway import ModelGateway


def _bundle(**overrides) -> EvidenceBundle:
    base = dict(
        decision={"decision_id": "eval-decision", "decision_cycle_id": "eval-cycle", "status": "proposed", "binding_constraints": []},
        telemetry=[], forecasts=[], assets=[], batteries=[], constraints=[], objective_policy={}, actions=[],
    )
    base.update(overrides)
    return EvidenceBundle(**base)


@dataclass
class EvalCase:
    name: str
    agent: str
    tier: str  # "structural" | "behavioral"
    run: Callable[[ModelGateway, str], object]
    check: Callable[[object], bool]
    description: str


def _stale_telemetry_bundle() -> EvidenceBundle:
    now = datetime.now(timezone.utc).isoformat()
    readings = [
        {"evidence_id": f"tel-{i:04d}", "asset_id": f"asset-{i}", "metric": "power_kw", "value": 10.0, "unit": "kW",
         "quality": "bad", "freshness": "stale", "confidence": 0.2, "age_seconds": 3600.0}
        for i in range(8)
    ]
    return _bundle(telemetry=readings, decision={"decision_id": "eval-decision", "decision_cycle_id": "eval-cycle", "status": "proposed", "binding_constraints": [], "as_of": now})


def _single_battery_concentration_bundle() -> EvidenceBundle:
    battery = {"evidence_id": "batt-0000", "asset_id": "battery-1", "soh_pct": 98.0, "energy_capacity_kwh": 500.0,
               "power_limit_kw": 200.0, "soc_min_pct": 10.0, "soc_max_pct": 95.0, "warranty_cycles_remaining": 4000}
    actions = [
        {"evidence_id": f"act-{i:04d}", "asset_id": "battery-1", "action_type": "discharge", "quantity": 150.0,
         "unit": "kW", "risk_level": "medium", "reason": "meet evening peak demand"}
        for i in range(6)
    ]
    return _bundle(batteries=[battery], actions=actions)


def _unauthorised_action_bundle() -> EvidenceBundle:
    actions = [{"evidence_id": "act-0000", "asset_id": "grid-1", "action_type": "sell", "quantity": 5000.0,
                "unit": "kW", "risk_level": "high", "reason": "export at high export price"}]
    return _bundle(actions=actions, constraints=[])


def _missing_forecast_bundle() -> EvidenceBundle:
    """A solar asset the optimizer must forecast, with zero forecast rows
    for it at all — per forecast_agent's own prompt, this is at least a
    MEDIUM finding (the optimizer would have had to treat it as zero)."""
    assets = [{"evidence_id": "ast-0000", "asset_id": "solar-1", "name": "Solar Farm 1",
               "asset_type": "solar", "rated_capacity_kw": 40_000, "availability": 1.0}]
    return _bundle(assets=assets, forecasts=[])


def _over_nameplate_asset_bundle() -> EvidenceBundle:
    """A 1,000kW-rated asset reporting 5,000kW of real-time power — per
    asset_agent's own prompt, real-time behaviour inconsistent with
    nameplate rating is exactly what it must flag."""
    assets = [{"evidence_id": "ast-0000", "asset_id": "wind-1", "name": "Wind Farm 1",
               "asset_type": "wind", "rated_capacity_kw": 1_000, "availability": 1.0}]
    telemetry = [{"evidence_id": "tel-0000", "asset_id": "wind-1", "metric": "power_kw", "value": 5_000.0,
                  "unit": "kW", "quality": "good", "freshness": "fresh", "confidence": 0.95, "age_seconds": 5.0}]
    return _bundle(assets=assets, telemetry=telemetry)


def _flat_price_series_bundle() -> EvidenceBundle:
    """Structural-only fixture for market_agent (its evidence is a thin,
    free-form decision summary rather than a clear numeric mismatch like the
    other agents get, so no behavioral case is claimed for it — see the
    docstring above on why every behavioral case here needs a genuinely
    detectable signal, not a guessed one)."""
    forecasts = [{"evidence_id": f"fc-{i:04d}", "variable": "price", "quantile": 0.5, "valid_time": f"2026-06-01T{i:02d}:00:00Z",
                  "value": 65.0, "unit": "GBP/MWh", "is_fallback": False, "model_version": "baseline-v1"} for i in range(6)]
    return _bundle(forecasts=forecasts, decision={"decision_id": "eval-decision", "decision_cycle_id": "eval-cycle",
                                                   "status": "proposed", "binding_constraints": [], "n_plan_steps": 6})


def _binding_export_limit_bundle() -> EvidenceBundle:
    """The grid export limit shows up as a binding constraint for every
    single step of the plan — per grid_agent's own prompt, a plan that
    binds the export limit for many consecutive hours (leaving no headroom
    for an unplanned event) is worth flagging even though it's not itself a
    violation."""
    constraints = [{"evidence_id": "con-0000", "scope": "asset:grid-1", "constraint_type": "grid_import_export_limit",
                     "expression": {"max_import_kw": 120_000, "max_export_kw": 100_000}, "is_hard": True, "source": "grid-code"}]
    decision = {"decision_id": "eval-decision", "decision_cycle_id": "eval-cycle", "status": "proposed",
                "binding_constraints": ["grid:export_limit"] * 24}
    return _bundle(constraints=constraints, decision=decision)


def _infeasible_solver_status_bundle() -> EvidenceBundle:
    """Per optimisation_reviewer's own prompt: if the solver status is not
    OPTIMAL, that alone is at least a MEDIUM finding."""
    decision = {"decision_id": "eval-decision", "decision_cycle_id": "eval-cycle", "status": "proposed",
                "binding_constraints": [], "solver_status": "INFEASIBLE", "objective_value": None}
    return _bundle(decision=decision)


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        name="data_quality_schema_valid",
        agent="data_quality",
        tier="structural",
        run=lambda gw, tenant_id: assess_data_quality(gw, _stale_telemetry_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="data_quality agent returns a schema-valid AgentEnvelope for a normal evidence bundle.",
    ),
    EvalCase(
        name="data_quality_flags_majority_stale_telemetry",
        agent="data_quality",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_data_quality(gw, _stale_telemetry_bundle(), tenant_id, "eval-corr"),
        check=lambda r: any(f.severity in ("HIGH", "CRITICAL") for f in r.findings),
        description="When every reading in the snapshot is stale/bad quality, the agent must raise at least one HIGH/CRITICAL finding rather than staying silent.",
    ),
    EvalCase(
        name="risk_critic_schema_valid",
        agent="risk_critic",
        tier="structural",
        run=lambda gw, tenant_id: assess_risk_critic(gw, _single_battery_concentration_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="risk_critic agent returns a schema-valid AgentEnvelope for a normal evidence bundle.",
    ),
    EvalCase(
        name="risk_critic_flags_single_battery_concentration",
        agent="risk_critic",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_risk_critic(gw, _single_battery_concentration_bundle(), tenant_id, "eval-corr"),
        check=lambda r: any(f.severity in ("MEDIUM", "HIGH", "CRITICAL") for f in r.findings),
        description="When every proposed action concentrates on the portfolio's single battery with no fallback, the adversarial risk_critic must raise at least a MEDIUM finding.",
    ),
    EvalCase(
        name="governance_schema_valid",
        agent="governance",
        tier="structural",
        run=lambda gw, tenant_id: assess_governance(gw, _unauthorised_action_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, GovernanceAssessment) and r.risk_tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
        description="governance agent returns a schema-valid GovernanceAssessment for a normal evidence bundle.",
    ),
    EvalCase(
        name="governance_requires_approval_when_no_constraint_authorises_action",
        agent="governance",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_governance(gw, _unauthorised_action_bundle(), tenant_id, "eval-corr"),
        check=lambda r: r.requires_human_approval is True,
        description="Per the agent's own prompt ('the absence of a rule is not a rule permitting the action'), a high-risk action with zero supporting constraints must require human approval.",
    ),
    EvalCase(
        name="forecast_schema_valid",
        agent="forecast",
        tier="structural",
        run=lambda gw, tenant_id: assess_forecast(gw, _missing_forecast_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="forecast agent returns a schema-valid AgentEnvelope for a normal evidence bundle.",
    ),
    EvalCase(
        name="forecast_flags_asset_with_no_forecast_series",
        agent="forecast",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_forecast(gw, _missing_forecast_bundle(), tenant_id, "eval-corr"),
        check=lambda r: any(f.severity in ("MEDIUM", "HIGH", "CRITICAL") for f in r.findings),
        description="Per the agent's own prompt, a solar asset with zero forecast rows must be flagged at least MEDIUM (the optimizer would have treated it as zero).",
    ),
    EvalCase(
        name="asset_schema_valid",
        agent="asset",
        tier="structural",
        run=lambda gw, tenant_id: assess_asset(gw, _over_nameplate_asset_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="asset agent returns a schema-valid AgentEnvelope for a normal evidence bundle.",
    ),
    EvalCase(
        name="asset_flags_telemetry_above_nameplate_rating",
        agent="asset",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_asset(gw, _over_nameplate_asset_bundle(), tenant_id, "eval-corr"),
        check=lambda r: any(f.severity in ("MEDIUM", "HIGH", "CRITICAL") for f in r.findings),
        description="A 1,000kW-rated asset reporting 5,000kW must be flagged as inconsistent with its nameplate rating.",
    ),
    EvalCase(
        name="market_schema_valid",
        agent="market",
        tier="structural",
        run=lambda gw, tenant_id: assess_market(gw, _flat_price_series_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="market agent returns a schema-valid AgentEnvelope for a normal evidence bundle (no behavioral case: this agent's evidence is a thin, free-form decision summary with no clean numeric mismatch to assert on).",
    ),
    EvalCase(
        name="grid_schema_valid",
        agent="grid",
        tier="structural",
        run=lambda gw, tenant_id: assess_grid(gw, _binding_export_limit_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="grid agent returns a schema-valid AgentEnvelope for a normal evidence bundle.",
    ),
    EvalCase(
        name="grid_flags_export_limit_binding_for_many_consecutive_hours",
        agent="grid",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_grid(gw, _binding_export_limit_bundle(), tenant_id, "eval-corr"),
        check=lambda r: any(f.severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL") for f in r.findings),
        description="Per the agent's own prompt, an export limit binding for 24 consecutive plan steps (no headroom for an unplanned event) is worth at least a LOW finding.",
    ),
    EvalCase(
        name="optimisation_reviewer_schema_valid",
        agent="optimisation_reviewer",
        tier="structural",
        run=lambda gw, tenant_id: assess_optimisation_reviewer(gw, _infeasible_solver_status_bundle(), tenant_id, "eval-corr"),
        check=lambda r: isinstance(r, AgentEnvelope) and r.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK"),
        description="optimisation_reviewer returns a schema-valid AgentEnvelope for a normal evidence bundle.",
    ),
    EvalCase(
        name="optimisation_reviewer_flags_non_optimal_solver_status",
        agent="optimisation_reviewer",
        tier="behavioral",
        run=lambda gw, tenant_id: assess_optimisation_reviewer(gw, _infeasible_solver_status_bundle(), tenant_id, "eval-corr"),
        check=lambda r: any(f.severity in ("MEDIUM", "HIGH", "CRITICAL") for f in r.findings),
        description="Per the agent's own prompt, a solver_status of INFEASIBLE (not OPTIMAL) must be flagged at least MEDIUM.",
    ),
]


def run_eval_suite(gateway: ModelGateway, tenant_id: str = "00000000-0000-0000-0000-000000000000") -> dict:
    """Runs every case above against `gateway`. Returns a dict shaped for
    `AgentEvalRun` (model_provider, total_cases, passed_cases, results).
    `tenant_id` is only used to tag the resulting `AgentCallLog` rows
    correctly (a valid tenant UUID — not a real query filter within the
    harness itself, which never touches the DB)."""
    results = []
    passed = 0
    for case in EVAL_CASES:
        if case.tier == "behavioral" and gateway.provider_name == "mock":
            results.append({
                "case": case.name, "agent": case.agent, "tier": case.tier, "outcome": "skipped",
                "detail": "behavioral cases require live model reasoning — mock gateway fills only schema-required fields generically",
            })
            continue
        try:
            result = case.run(gateway, tenant_id)
            ok = case.check(result)
            results.append({
                "case": case.name, "agent": case.agent, "tier": case.tier,
                "outcome": "passed" if ok else "failed",
                "detail": case.description,
            })
            if ok:
                passed += 1
        except GatewayError as exc:
            results.append({
                "case": case.name, "agent": case.agent, "tier": case.tier, "outcome": "failed",
                "detail": f"agent failed closed (schema-invalid after retry): {exc}",
            })
        except Exception as exc:  # a case itself erroring is still a real finding, not a harness bug to hide
            results.append({
                "case": case.name, "agent": case.agent, "tier": case.tier, "outcome": "failed",
                "detail": f"eval case raised: {exc}",
            })

    scored = [r for r in results if r["outcome"] != "skipped"]
    return {
        "model_provider": gateway.provider_name,
        "total_cases": len(scored),
        "passed_cases": passed,
        "results": results,
    }
