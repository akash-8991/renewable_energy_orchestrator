"""Portfolio-wide start/stop: whether the optimizer's decision cycle is
actually running for this tenant. Defaults to idle (see Tenant.operating_state
in models/canonical.py) — a fresh deploy or a cleared database does not start
making decisions on its own.

Starting requires at least one active `database` or `data_table` connector
in Connector Studio — the two kinds actually wired to real data ingestion
(see routers/connectors.py). The other four connector kinds (generic,
market_energy_purchase, scada, iot) are for agents to act *out* on the
world once a decision is made, not for bringing data *in*, so they
deliberately don't count here — by explicit request. Document Intake
uploads (documents, generic files, the reference dataset, the generic
mapping agent) still ingest real data exactly as before; they just no
longer satisfy this gate or auto-start the optimizer on their own — a
database/data_table connector must be active first.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.security import AuthContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.canonical import Connector, DocumentIntake, Tenant
from output.audit import append_audit_event

from ..deps import db_session, require_permission

router = APIRouter(prefix="/operations", tags=["operations"])


DATA_INGESTION_CONNECTOR_KINDS = ("database", "data_table")


class OperationsStatus(BaseModel):
    operating_state: str
    operating_state_changed_at: str | None
    has_data_source: bool
    active_connector_count: int  # active database/data_table connectors only — see DATA_INGESTION_CONNECTOR_KINDS
    document_count: int


def _status(db: Session, tenant: Tenant) -> OperationsStatus:
    active_connectors = db.execute(
        select(func.count()).select_from(Connector).where(
            Connector.tenant_id == tenant.id,
            Connector.status == "active",
            Connector.kind.in_(DATA_INGESTION_CONNECTOR_KINDS),
        )
    ).scalar_one()
    documents = db.execute(
        select(func.count()).select_from(DocumentIntake).where(DocumentIntake.tenant_id == tenant.id)
    ).scalar_one()
    return OperationsStatus(
        operating_state=tenant.operating_state,
        operating_state_changed_at=tenant.operating_state_changed_at.isoformat() if tenant.operating_state_changed_at else None,
        has_data_source=(active_connectors > 0),
        active_connector_count=active_connectors,
        document_count=documents,
    )


def _get_tenant(db: Session, tenant_id: str) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.id == tenant_id)).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    return tenant


@router.get("/status", response_model=OperationsStatus)
def get_status(
    ctx: AuthContext = Depends(require_permission("read:dashboard")), db: Session = Depends(db_session)
) -> OperationsStatus:
    return _status(db, _get_tenant(db, ctx.tenant_id))


@router.post("/start", response_model=OperationsStatus)
def start_operations(
    ctx: AuthContext = Depends(require_permission("manage:policies")), db: Session = Depends(db_session)
) -> OperationsStatus:
    tenant = _get_tenant(db, ctx.tenant_id)
    current = _status(db, tenant)
    if not current.has_data_source:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no data source connected — activate a database or data_table connector in Connector Studio before "
            "starting the optimizer (Document Intake uploads still ingest data, but no longer unlock this on their own)",
        )
    tenant.operating_state = "running"
    tenant.operating_state_changed_at = datetime.now(timezone.utc)
    db.flush()
    append_audit_event(
        db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
        event_type="operations.started", payload={"active_connector_count": current.active_connector_count, "document_count": current.document_count},
    )
    db.commit()
    return _status(db, tenant)


def mark_started_if_idle(db: Session, tenant: Tenant, *, actor_id: str, actor_label: str, reason: str) -> bool:
    """Called by connectors.py's ingest_connector() after a database/
    data_table connector successfully ingests — auto-starts the optimizer
    the first time real data flows through one of the two connector kinds
    that gate Start (see DATA_INGESTION_CONNECTOR_KINDS above), rather than
    making the user separately click Start right after. Deliberately NOT
    called from ingestion.py's Document Intake uploads — those still ingest
    real data, they just don't gate or auto-start the optimizer, by explicit
    request. Returns True if it actually flipped the state (idempotent — a
    second ingest while already running is a no-op)."""
    if tenant.operating_state == "running":
        return False
    tenant.operating_state = "running"
    tenant.operating_state_changed_at = datetime.now(timezone.utc)
    db.flush()
    append_audit_event(
        db, tenant_id=tenant.id, actor_id=actor_id, actor_label=actor_label,
        event_type="operations.auto_started", payload={"reason": reason},
    )
    return True


@router.post("/stop", response_model=OperationsStatus)
def stop_operations(
    ctx: AuthContext = Depends(require_permission("manage:policies")), db: Session = Depends(db_session)
) -> OperationsStatus:
    tenant = _get_tenant(db, ctx.tenant_id)
    tenant.operating_state = "idle"
    tenant.operating_state_changed_at = datetime.now(timezone.utc)
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email, event_type="operations.stopped", payload={})
    db.commit()
    return _status(db, tenant)
