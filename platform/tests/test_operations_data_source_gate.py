"""Start Optimizer's data-source gate (routers/operations.py's
DATA_INGESTION_CONNECTOR_KINDS): only an active `database` or `data_table`
connector counts. The other four kinds (generic/market_energy_purchase/
scada/iot) are for agents to act *out* on the world once a decision is
made, not for bringing data in, so an active one of those must NOT unlock
Start Optimizer — this is the regression this file exists to guard,
by explicit request."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import contextlib

from app.routers.operations import _status  # noqa: E402
from database.connection import reset_current_tenant, set_current_tenant  # noqa: E402
from models.canonical import Connector  # noqa: E402


@contextlib.contextmanager
def _tenant_context(tenant_id: str):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def _connector(tenant_id: str, kind: str, status: str = "active") -> Connector:
    return Connector(tenant_id=tenant_id, name=f"test-{kind}", kind=kind, endpoint_url="https://example.com", status=status)


def test_no_connectors_means_no_data_source(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        result = _status(db_session, t1)
    assert result.has_data_source is False
    assert result.active_connector_count == 0


def test_active_database_connector_unlocks_data_source(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        db_session.add(_connector(t1.id, "database"))
        db_session.flush()
        result = _status(db_session, t1)
    assert result.has_data_source is True
    assert result.active_connector_count == 1


def test_active_data_table_connector_unlocks_data_source(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        db_session.add(_connector(t1.id, "data_table"))
        db_session.flush()
        result = _status(db_session, t1)
    assert result.has_data_source is True


def test_active_generic_connector_does_not_unlock_data_source(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        db_session.add(_connector(t1.id, "generic"))
        db_session.flush()
        result = _status(db_session, t1)
    assert result.has_data_source is False
    assert result.active_connector_count == 0


def test_active_scada_market_and_iot_connectors_do_not_unlock_data_source(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        for kind in ("scada", "market_energy_purchase", "iot"):
            db_session.add(_connector(t1.id, kind))
        db_session.flush()
        result = _status(db_session, t1)
    assert result.has_data_source is False


def test_inactive_database_connector_does_not_unlock_data_source(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        db_session.add(_connector(t1.id, "database", status="draft"))
        db_session.flush()
        result = _status(db_session, t1)
    assert result.has_data_source is False


def test_document_count_is_still_reported_but_does_not_gate_data_source(db_session, two_tenants):
    """Document Intake uploads still show up in document_count (informational)
    but must not, by themselves, flip has_data_source True."""
    t1, _ = two_tenants
    with _tenant_context(t1.id):
        result = _status(db_session, t1)
    assert result.document_count == 0  # no documents seeded for this throwaway tenant
    assert result.has_data_source is False
