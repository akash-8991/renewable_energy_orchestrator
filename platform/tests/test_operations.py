"""Portfolio-wide start/stop gate: a fresh/cleared tenant defaults to idle
(policy/cycle.py skips the decision cycle entirely for it), and
mark_started_if_idle() is what auto-starts a tenant the first time a
document/dataset actually ingests successfully."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.routers.operations import mark_started_if_idle  # noqa: E402

_ACTOR_ID = str(uuid.uuid4())


def test_tenant_defaults_to_idle(two_tenants):
    t1, t2 = two_tenants
    assert t1.operating_state == "idle"
    assert t2.operating_state == "idle"


def test_mark_started_if_idle_flips_idle_to_running(db_session, two_tenants):
    t1, _ = two_tenants
    changed = mark_started_if_idle(db_session, t1, actor_id=_ACTOR_ID, actor_label="test@example.com", reason="test upload")
    assert changed is True
    assert t1.operating_state == "running"
    assert t1.operating_state_changed_at is not None


def test_mark_started_if_idle_is_a_noop_once_running(db_session, two_tenants):
    t1, _ = two_tenants
    mark_started_if_idle(db_session, t1, actor_id=_ACTOR_ID, actor_label="test@example.com", reason="first upload")
    changed_at_first_start = t1.operating_state_changed_at

    changed = mark_started_if_idle(db_session, t1, actor_id=_ACTOR_ID, actor_label="test@example.com", reason="second upload")
    assert changed is False
    assert t1.operating_state == "running"
    assert t1.operating_state_changed_at == changed_at_first_start


def test_mark_started_if_idle_does_not_affect_other_tenants(db_session, two_tenants):
    t1, t2 = two_tenants
    mark_started_if_idle(db_session, t1, actor_id=_ACTOR_ID, actor_label="test@example.com", reason="upload")
    assert t1.operating_state == "running"
    assert t2.operating_state == "idle"
