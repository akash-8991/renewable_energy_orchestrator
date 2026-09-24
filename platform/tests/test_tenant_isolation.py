"""BR-07 / FR-MT-001: a query that forgets to filter by tenant must never
leak another tenant's rows, and must fail closed with no tenant context."""

from reo_common.db import reset_current_tenant, set_current_tenant
from reo_common.models import Portfolio
from sqlalchemy import select


def test_tenant_scoped_query_only_sees_own_tenant(db_session, two_tenants):
    t1, t2 = two_tenants

    token = set_current_tenant(t1.id)
    try:
        # deliberately NOT filtering by tenant_id in the query — the event
        # listener must inject the predicate anyway
        rows = db_session.execute(select(Portfolio)).scalars().all()
    finally:
        reset_current_tenant(token)

    assert all(r.tenant_id == t1.id for r in rows)
    assert not any(r.tenant_id == t2.id for r in rows)


def test_no_tenant_context_fails_closed(db_session, two_tenants):
    token = set_current_tenant(None)
    try:
        rows = db_session.execute(select(Portfolio)).scalars().all()
    finally:
        reset_current_tenant(token)

    assert rows == []


def test_break_glass_sees_across_tenants(db_session, two_tenants):
    from reo_common.db import break_glass_cross_tenant

    t1, t2 = two_tenants
    with break_glass_cross_tenant():
        rows = db_session.execute(select(Portfolio)).scalars().all()

    tenant_ids = {r.tenant_id for r in rows}
    assert t1.id in tenant_ids
    assert t2.id in tenant_ids
