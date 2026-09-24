"""FR-AU-001 / TR-AUD-01: the audit log must be tamper-evident — a row edited
after the fact breaks the hash chain at that point."""

from reo_common.audit import append_audit_event, verify_chain
from reo_common.models import AuditEvent
from sqlalchemy import select


def test_chain_verifies_when_untouched(db_session, two_tenants):
    t1, _ = two_tenants
    for i in range(3):
        append_audit_event(
            db_session, tenant_id=t1.id, actor_id=None, actor_label="test-actor",
            event_type=f"test.event.{i}", payload={"i": i},
        )
    db_session.flush()

    events = (
        db_session.execute(select(AuditEvent).where(AuditEvent.tenant_id == t1.id).order_by(AuditEvent.created_at))
        .scalars().all()
    )
    ok, broken_id = verify_chain(events)
    assert ok, f"chain broken at {broken_id}"


def test_chain_detects_tampering(db_session, two_tenants):
    t1, _ = two_tenants
    append_audit_event(
        db_session, tenant_id=t1.id, actor_id=None, actor_label="test-actor",
        event_type="test.event.tamper", payload={"amount": 100},
    )
    db_session.flush()

    events = (
        db_session.execute(select(AuditEvent).where(AuditEvent.tenant_id == t1.id).order_by(AuditEvent.created_at))
        .scalars().all()
    )
    events[-1].payload = {"amount": 999999}  # tamper in-memory, without updating the hash

    ok, broken_id = verify_chain(events)
    assert not ok
    assert broken_id == events[-1].id


def test_a_trailing_slice_of_a_valid_chain_must_not_be_verified_on_its_own(db_session, two_tenants):
    """Regression test: GET /audit/events used to run verify_chain() on an
    already-sliced `rows[-limit:]` (or an event_type-filtered) view. Slicing
    removes the earlier events a later one's prev_hash points to, so
    verify_chain — which always starts from GENESIS_HASH — correctly
    reports a perfectly untampered chain as broken. This documents why: the
    fix is to always verify the full chain and slice only for display."""
    t1, _ = two_tenants
    for i in range(5):
        append_audit_event(
            db_session, tenant_id=t1.id, actor_id=None, actor_label="test-actor",
            event_type=f"test.event.{i}", payload={"i": i},
        )
    db_session.flush()

    full_chain = (
        db_session.execute(select(AuditEvent).where(AuditEvent.tenant_id == t1.id).order_by(AuditEvent.created_at))
        .scalars().all()
    )
    ok_full, _ = verify_chain(full_chain)
    assert ok_full

    sliced = full_chain[-2:]
    ok_sliced, broken_id = verify_chain(sliced)
    assert not ok_sliced, "a trailing slice of a valid chain should fail verify_chain on its own — this is expected, not a tamper signal"
    assert broken_id == sliced[0].id
