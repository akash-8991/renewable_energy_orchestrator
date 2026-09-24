"""Policy/safety engine core (FR-GV-001/002/003): resolves the effective
autonomy mode for a decision, and classifies action risk so the engine
knows which actions AUTONOMOUS_BOUNDED mode is actually allowed to execute
unattended.

Simplification (docs/SIMPLIFICATIONS.md): scope resolution supports
"portfolio" (tenant-wide) and "asset:<id>" — site-level and asset-type-level
scopes are modeled in the schema (any string scope works) but this
resolver only implements the two most common cases end-to-end; extending
the match order below to site/asset_type scopes is a small, contained
change when needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Action, AutonomyPolicy

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
DEFAULT_MODE = "OBSERVE"  # fail-safe default with no policy configured (BR-05: degrade to a safer mode)


def resolve_autonomy_mode(db: Session, tenant_id: str, asset_id: str | None = None) -> tuple[str, AutonomyPolicy | None]:
    """Most specific effective policy wins: asset-scoped over portfolio-wide.
    Returns (mode, policy_row_or_None). No configured policy -> DEFAULT_MODE,
    which is deliberately the most conservative mode, not the most
    permissive — the absence of a policy must never be read as permission.
    """
    now = datetime.now(timezone.utc)

    def _active(scope: str) -> AutonomyPolicy | None:
        stmt = (
            select(AutonomyPolicy)
            .where(
                AutonomyPolicy.tenant_id == tenant_id,
                AutonomyPolicy.scope == scope,
                AutonomyPolicy.effective_from <= now,
            )
            .where((AutonomyPolicy.effective_to.is_(None)) | (AutonomyPolicy.effective_to > now))
            .order_by(AutonomyPolicy.effective_from.desc())
        )
        return db.execute(stmt).scalars().first()

    if asset_id:
        policy = _active(f"asset:{asset_id}")
        if policy:
            return policy.mode, policy

    policy = _active("portfolio")
    if policy:
        return policy.mode, policy

    return DEFAULT_MODE, None


def classify_risk(action_type: str, quantity_kw: float, rated_capacity_kw: float, binding_constraints: list[str], asset_id: str | None) -> str:
    """A simple, explainable heuristic — not a substitute for a real hazard
    analysis (doc 05 §7), which is why AUTONOMOUS_BOUNDED additionally
    requires a recorded safety_case_ref regardless of what this classifies
    an individual action as."""
    if rated_capacity_kw <= 0:
        return "medium"
    utilisation = abs(quantity_kw) / rated_capacity_kw

    asset_is_binding = bool(asset_id) and any(asset_id in c for c in binding_constraints)
    if asset_is_binding and utilisation > 0.7:
        return "high"
    if utilisation > 0.85:
        return "high"
    if utilisation > 0.5 or asset_is_binding:
        return "medium"
    return "low"


def autonomous_execution_allowed(policy: AutonomyPolicy | None, action_risk: str) -> bool:
    """AUTONOMOUS_BOUNDED still needs BOTH a recorded safety case AND the
    action's risk to be within the policy's configured ceiling — doc 05
    §7's "formal hazard analysis... required before autonomous production"
    is enforced here structurally, not left to convention."""
    if policy is None or policy.mode != "AUTONOMOUS_BOUNDED":
        return False
    if not policy.safety_case_ref:
        return False
    return RISK_ORDER.get(action_risk, 99) <= RISK_ORDER.get(policy.max_action_risk, 0)
