import os
import sys
import uuid

import pytest

# platform root: makes the top-level shared packages (models, database,
# guardrails, evaluation, policy, output) importable as e.g.
# `from models.canonical import X` from any test, matching how every
# service's own PYTHONPATH is set up in its Dockerfile.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "reo_common"))

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://reo:reo@127.0.0.1:5433/reo")
os.environ.setdefault("ASYNC_DATABASE_URL", "postgresql+asyncpg://reo:reo@127.0.0.1:5433/reo")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6380/0")

from database.connection import SessionLocal, break_glass_cross_tenant, reset_current_tenant, set_current_tenant  # noqa: E402
from models.canonical import Portfolio, Tenant  # noqa: E402


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def two_tenants(db_session):
    """Create two throwaway tenants with one Portfolio each, for isolation tests."""
    with break_glass_cross_tenant():
        t1 = Tenant(slug=f"test-{uuid.uuid4().hex[:8]}", name="Test Tenant 1")
        t2 = Tenant(slug=f"test-{uuid.uuid4().hex[:8]}", name="Test Tenant 2")
        db_session.add_all([t1, t2])
        db_session.flush()

        token = set_current_tenant(t1.id)
        db_session.add(Portfolio(tenant_id=t1.id, name="Tenant 1 Portfolio"))
        reset_current_tenant(token)

        token = set_current_tenant(t2.id)
        db_session.add(Portfolio(tenant_id=t2.id, name="Tenant 2 Portfolio"))
        reset_current_tenant(token)

        db_session.flush()

    # yield OUTSIDE the break_glass context — otherwise isolation would be
    # (invisibly) disabled for the entire test body, defeating the point.
    yield t1, t2
    # throwaway tenants — no cleanup needed for local/test DBs between runs,
    # but roll back so we never actually commit test fixtures
    db_session.rollback()
