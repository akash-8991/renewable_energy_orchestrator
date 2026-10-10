"""Platform admin is the super user: besides holding every permission it is not
held to two separation-of-duties rules that bind everyone else — maker-checker
on connector activation, and the no-self-approval rule — and each use of that
latitude is recorded in the audit log. Everyone else stays bound."""

import contextlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers.connectors import activate_connector  # noqa: E402
from app.routers.governance import ApprovalDecisionRequest, decide_approval  # noqa: E402
from database.connection import reset_current_tenant, set_current_tenant  # noqa: E402
from models.canonical import Approval, AuditEvent, Connector, Decision, Portfolio, Role, User  # noqa: E402
from reo_common.security import AuthContext, PERMISSIONS  # noqa: E402


@contextlib.contextmanager
def _tenant(tenant_id):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def _user(db, tenant, email):
    u = User(tenant_id=tenant.id, email=email, display_name=email, hashed_password="x", roles=[])
    db.add(u)
    db.flush()
    return u


def _ctx(user, tenant, *roles):
    return AuthContext(user_id=user.id, tenant_id=tenant.id, roles=list(roles), email=user.email)


def _events(db, tenant, event_type):
    return [e.payload for e in db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id, AuditEvent.event_type == event_type)).scalars()]


# --- maker-checker ---------------------------------------------------------------------------


def _connector(db, tenant, creator):
    c = Connector(tenant_id=tenant.id, name=f"c-{uuid.uuid4().hex[:6]}", kind="data_table", endpoint_url="x.csv", status="testing",
                  created_by=creator.id, approval_policy={"requires_maker_checker": True})
    db.add(c)
    db.flush()
    return c


def test_a_tenant_admin_still_cannot_activate_their_own_connector(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        me = _user(db_session, t1, "tenant-admin@test.example")
        connector = _connector(db_session, t1, me)
        with pytest.raises(HTTPException) as exc:
            activate_connector(connector.id, ctx=_ctx(me, t1, Role.TENANT_ADMIN.value), db=db_session)
        assert exc.value.status_code == 403 and connector.status == "testing"


def test_a_platform_admin_can_activate_their_own_connector_and_it_is_recorded(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        me = _user(db_session, t1, "platform-admin@test.example")
        connector = _connector(db_session, t1, me)
        out = activate_connector(connector.id, ctx=_ctx(me, t1, Role.PLATFORM_ADMIN.value), db=db_session)
        assert out.status == "active"
        payload = _events(db_session, t1, "connector.activated")[-1]
        assert payload["maker_checker_overridden_by_platform_admin"] is True


def test_a_normal_activation_by_a_different_user_is_not_flagged_as_an_override(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        maker, checker = _user(db_session, t1, "maker@test.example"), _user(db_session, t1, "checker@test.example")
        connector = _connector(db_session, t1, maker)
        activate_connector(connector.id, ctx=_ctx(checker, t1, Role.PLATFORM_ADMIN.value), db=db_session)
        assert "maker_checker_overridden_by_platform_admin" not in _events(db_session, t1, "connector.activated")[-1]


# --- self-approval -----------------------------------------------------------------------------


def _pending_approval(db, tenant, prior_approver):
    portfolio = db.execute(select(Portfolio).where(Portfolio.tenant_id == tenant.id)).scalars().first()
    decision = Decision(tenant_id=tenant.id, portfolio_id=portfolio.id, decision_cycle_id=f"cyc-{uuid.uuid4().hex[:6]}", version=1,
                        trigger="scheduled", horizon="24h", autonomy_mode="APPROVAL_REQUIRED", status="proposed", plan={})
    db.add(decision)
    db.flush()
    approval = Approval(tenant_id=tenant.id, decision_id=decision.id, approver_id=prior_approver.id, outcome="pending",
                        token=f"t-{uuid.uuid4().hex}", expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    db.add(approval)
    db.flush()
    return approval


def test_an_operator_cannot_decide_an_item_they_already_decided_on(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        me = _user(db_session, t1, "operator@test.example")
        approval = _pending_approval(db_session, t1, me)
        with pytest.raises(HTTPException) as exc:
            decide_approval(approval.id, ApprovalDecisionRequest(outcome="rejected", reason="r"), ctx=_ctx(me, t1, Role.SENIOR_OPERATOR.value), db=db_session)
        assert exc.value.status_code == 403 and "self-approval" in exc.value.detail


def test_a_platform_admin_can_and_the_override_is_recorded(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        me = _user(db_session, t1, "platform-admin@test.example")
        approval = _pending_approval(db_session, t1, me)
        out = decide_approval(approval.id, ApprovalDecisionRequest(outcome="rejected", reason="super user"), ctx=_ctx(me, t1, Role.PLATFORM_ADMIN.value), db=db_session)
        assert out.outcome == "rejected"
        assert _events(db_session, t1, "approval.decided")[-1]["self_approval_overridden_by_platform_admin"] is True


def test_a_normal_decision_carries_no_override_flag(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        first, second = _user(db_session, t1, "first@test.example"), _user(db_session, t1, "second@test.example")
        approval = _pending_approval(db_session, t1, first)
        decide_approval(approval.id, ApprovalDecisionRequest(outcome="rejected", reason="ok"), ctx=_ctx(second, t1, Role.PLATFORM_ADMIN.value), db=db_session)
        assert "self_approval_overridden_by_platform_admin" not in _events(db_session, t1, "approval.decided")[-1]


# --- the rest of the model is unchanged ---------------------------------------------------------------


def test_platform_admin_holds_every_permission_that_exists():
    everything = set().union(*PERMISSIONS.values())
    ctx = AuthContext(user_id="u", tenant_id="t", roles=[Role.PLATFORM_ADMIN.value], email="p@test.example")
    assert ctx.permissions == everything and ctx.is_platform_admin
    assert not AuthContext(user_id="u", tenant_id="t", roles=[Role.TENANT_ADMIN.value], email="t@test.example").is_platform_admin
