"""Policy/safety engine: risk classification and the structural requirement
that AUTONOMOUS_BOUNDED needs both a recorded safety case AND the action's
risk within the policy's ceiling (doc 05 §7)."""

from datetime import datetime, timedelta, timezone

from reo_common.autonomy import DEFAULT_MODE, autonomous_execution_allowed, classify_risk, resolve_autonomy_mode
from reo_common.db import reset_current_tenant, set_current_tenant
from reo_common.models import AutonomyPolicy


def test_classify_risk_scales_with_utilisation():
    assert classify_risk("charge", 100, 1000, [], "asset-1") == "low"
    assert classify_risk("charge", 600, 1000, [], "asset-1") == "medium"
    assert classify_risk("charge", 900, 1000, [], "asset-1") == "high"


def test_classify_risk_escalates_when_asset_is_binding():
    # 75% utilisation alone is "medium" (classify_risk's plain threshold is
    # 85% for "high"), but a binding constraint on this exact asset lowers
    # that threshold to 70% — margin against a limit matters, not just
    # raw magnitude.
    without_binding = classify_risk("discharge", 750, 1000, [], "asset-1")
    with_binding = classify_risk("discharge", 750, 1000, ["battery:asset-1:soc_bound"], "asset-1")
    assert without_binding == "medium"
    assert with_binding == "high"


def test_autonomous_requires_safety_case_ref():
    policy = AutonomyPolicy(mode="AUTONOMOUS_BOUNDED", max_action_risk="low", safety_case_ref=None)
    assert not autonomous_execution_allowed(policy, "low")


def test_autonomous_requires_mode_to_actually_be_autonomous():
    policy = AutonomyPolicy(mode="APPROVAL_REQUIRED", max_action_risk="low", safety_case_ref="SC-001")
    assert not autonomous_execution_allowed(policy, "low")


def test_autonomous_blocks_risk_above_ceiling():
    policy = AutonomyPolicy(mode="AUTONOMOUS_BOUNDED", max_action_risk="low", safety_case_ref="SC-001")
    assert autonomous_execution_allowed(policy, "low")
    assert not autonomous_execution_allowed(policy, "medium")
    assert not autonomous_execution_allowed(policy, "high")


def test_no_policy_configured_is_conservative_default(db_session, two_tenants):
    t1, _ = two_tenants
    token = set_current_tenant(t1.id)
    try:
        mode, policy = resolve_autonomy_mode(db_session, t1.id)
    finally:
        reset_current_tenant(token)
    assert mode == DEFAULT_MODE == "OBSERVE"
    assert policy is None


def test_asset_scoped_policy_beats_portfolio_wide(db_session, two_tenants):
    t1, _ = two_tenants
    token = set_current_tenant(t1.id)
    try:
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.add(AutonomyPolicy(tenant_id=t1.id, scope="portfolio", mode="RECOMMEND", effective_from=now))
        db_session.add(AutonomyPolicy(tenant_id=t1.id, scope="asset:battery-1", mode="APPROVAL_REQUIRED", effective_from=now))
        db_session.flush()

        portfolio_mode, _ = resolve_autonomy_mode(db_session, t1.id, asset_id="battery-2")
        asset_mode, _ = resolve_autonomy_mode(db_session, t1.id, asset_id="battery-1")
    finally:
        reset_current_tenant(token)

    assert portfolio_mode == "RECOMMEND"
    assert asset_mode == "APPROVAL_REQUIRED"
