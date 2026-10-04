"""Periodic pruning of high-volume, non-ledger tables.

Scenario runs (7 rows per cycle) and agent call logs (one per agent call per
cycle) are observability data that grow forever at a 2-minute cadence. They
are pruned after `OBSERVABILITY_RETENTION_DAYS` (default 90). The decision
ledger (decisions/actions/approvals/signals/commands) and the hash-chained
audit log are never pruned here — their retention is a legal/contractual
question (Tenant.retention_years), not a housekeeping one. Telemetry and
forecasts are handled by TimescaleDB retention policies (migrations 0003 and
0014)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from database.connection import SessionLocal, break_glass_cross_tenant
from models.canonical import AgentCallLog, ScenarioRun

log = logging.getLogger("optimizer-worker.housekeeping")


def prune_observability(retention_days: int, now: datetime | None = None) -> dict[str, int]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            removed = {
                "scenario_runs": db.execute(delete(ScenarioRun).where(ScenarioRun.created_at < cutoff)).rowcount or 0,
                "agent_call_logs": db.execute(delete(AgentCallLog).where(AgentCallLog.created_at < cutoff)).rowcount or 0,
            }
            db.commit()
        if any(removed.values()):
            log.info("pruned observability data older than %s days: %s", retention_days, removed)
        return removed
    except Exception:
        db.rollback()
        log.exception("housekeeping failed")
        return {}
    finally:
        db.close()
