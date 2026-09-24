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


def test_second_tenant_in_same_process_is_not_filtered_by_first_tenants_id(db_session, two_tenants):
    """Regression test: `with_loader_criteria`'s filter lambda used to bind
    the tenant id via a `lambda cls, tid=tenant_id: ...` default argument.
    SQLAlchemy's lambda-SQL cache only re-parameterises tracked *closure*
    variables per call, not default-argument values — a default argument is
    invisible to that tracking — so the first tenant_id this lambda's
    source ever compiled with was silently reused as the bound parameter
    for every later call in the same process, no matter which tenant was
    actually current. The practical effect: the second (and every
    subsequent) tenant to run a query in a long-lived process — e.g. the
    second request handled by a live api worker — got filtered by the
    *first* tenant's id and saw an empty result for its own data. This
    test queries as tenant 1, then as tenant 2, in the same session/process
    exactly as two sequential HTTP requests would, and asserts tenant 2
    still sees its own row.
    """
    t1, t2 = two_tenants

    token = set_current_tenant(t1.id)
    try:
        first_rows = db_session.execute(select(Portfolio)).scalars().all()
    finally:
        reset_current_tenant(token)
    assert len(first_rows) == 1 and first_rows[0].tenant_id == t1.id

    token = set_current_tenant(t2.id)
    try:
        second_rows = db_session.execute(select(Portfolio)).scalars().all()
    finally:
        reset_current_tenant(token)
    assert len(second_rows) == 1 and second_rows[0].tenant_id == t2.id
