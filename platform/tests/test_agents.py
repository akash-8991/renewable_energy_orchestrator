"""Each specialist agent module, exercised against a synthetic evidence
bundle with the deterministic mock model gateway — no DB, no API key.
Confirms every agent's distinct Pydantic response schema is genuinely
constructible (this is what would catch, e.g., a schema with a field type
OR-Tools/Anthropic's tool-forcing can't represent) and that the specialist
prompt-building code runs without error for realistic-shaped evidence."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "agent-worker"))

from agents.asset_agent import assess as assess_asset  # noqa: E402
from agents.base import AgentEnvelope  # noqa: E402
from agents.data_quality import assess as assess_data_quality  # noqa: E402
from agents.explanation_agent import OperatorExplanation, explain as run_explanation  # noqa: E402
from agents.forecast_agent import assess as assess_forecast  # noqa: E402
from agents.governance_agent import GovernanceAssessment, assess as assess_governance  # noqa: E402
from agents.grid_agent import assess as assess_grid  # noqa: E402
from agents.market_agent import assess as assess_market  # noqa: E402
from agents.optimisation_reviewer import assess as assess_optimisation  # noqa: E402
from agents.risk_critic import assess as assess_risk  # noqa: E402
from context import EvidenceBundle  # noqa: E402
from reo_common.model_gateway import MockModelGateway  # noqa: E402


def _synthetic_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        decision={
            "decision_id": "dec-1", "decision_cycle_id": "cyc-1", "version": 1, "trigger": "scheduled",
            "horizon": "24h", "status": "proposed", "confidence": 0.9, "binding_constraints": ["grid:export_limit"],
            "objective_value": -1234.5, "solver_status": "OPTIMAL", "alternatives": [], "n_plan_steps": 24,
        },
        telemetry=[{"evidence_id": "tel-0001", "asset_id": "asset-1", "metric": "power_kw", "value": 100.0, "unit": "kW", "quality": "good", "freshness": "fresh", "confidence": 0.98, "age_seconds": 1.0}],
        forecasts=[{"evidence_id": "fc-0001", "asset_id": "asset-1", "variable": "solar", "valid_time": "2026-09-24T12:00:00+00:00", "quantile": 0.5, "value": 500.0, "unit": "kW", "model_version": "baseline-v1", "is_fallback": False}],
        assets=[{"evidence_id": "asset-0001", "asset_id": "asset-1", "name": "Test Solar", "asset_type": "solar", "rated_capacity_kw": 1000.0, "availability": 1.0}],
        batteries=[{"evidence_id": "batt-0001", "asset_id": "batt-1", "soh_pct": 98.0, "energy_capacity_kwh": 1000, "power_limit_kw": 500, "soc_min_pct": 10, "soc_max_pct": 95, "warranty_cycles_remaining": 3000}],
        constraints=[{"evidence_id": "con-0001", "scope": "asset:grid-1", "constraint_type": "grid_import_export_limit", "expression": {"max_import_kw": 1000}, "is_hard": True}],
        objective_policy={"weights": {"cost": 0.5}, "carbon_price_per_tonne": 80, "risk_aversion": 0.2, "combination_method": "weighted_sum"},
        actions=[{"evidence_id": "act-0001", "asset_id": "batt-1", "action_type": "charge", "quantity": 100.0, "unit": "kW", "risk_level": "low", "reason": "test"}],
    )


def test_all_seven_envelope_agents_produce_valid_output():
    gateway = MockModelGateway()
    bundle = _synthetic_bundle()
    for fn in (assess_data_quality, assess_forecast, assess_asset, assess_market, assess_grid, assess_optimisation, assess_risk):
        result = fn(gateway, bundle, "tenant-1", "cyc-1")
        assert isinstance(result, AgentEnvelope)
        assert result.status in ("OK", "WARNING", "INSUFFICIENT_EVIDENCE", "POLICY_BLOCK")


def test_governance_agent_produces_valid_assessment():
    result = assess_governance(MockModelGateway(), _synthetic_bundle(), "tenant-1", "cyc-1")
    assert isinstance(result, GovernanceAssessment)
    assert result.risk_tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_explanation_agent_produces_valid_template():
    result = run_explanation(MockModelGateway(), _synthetic_bundle(), "tenant-1", "cyc-1")
    assert isinstance(result, OperatorExplanation)
