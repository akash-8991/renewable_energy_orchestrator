"""Canonical data model — TRD §3 core entities plus the v1.1 platform-layer
extensions (Signal, Connector, CredentialRef, ExportJob) and the RBAC/tenant
administration tables needed to run the platform (User, Role assignment).

Every tenant-scoped table subclasses `TenantScopedMixin` and is registered
with the tenant-isolation event listener in `db.py`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, register_tenant_scoped


def uuid_pk() -> Mapped[str]:
    return mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))


def tenant_fk() -> Mapped[str]:
    return mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)


class Role(str, enum.Enum):
    """FRD §4 roles, plus a platform-level role for the control plane."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    SENIOR_OPERATOR = "senior_operator"
    PORTFOLIO_MANAGER = "portfolio_manager"
    OT_ADMIN = "ot_admin"
    MODEL_ADMIN = "model_admin"
    TENANT_ADMIN = "tenant_admin"
    AUDITOR_DPO = "auditor_dpo"
    PLATFORM_ADMIN = "platform_admin"


class AutonomyMode(str, enum.Enum):
    OBSERVE = "OBSERVE"
    RECOMMEND = "RECOMMEND"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AUTONOMOUS_BOUNDED = "AUTONOMOUS_BOUNDED"


class DecisionStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXECUTED = "executed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    EXPIRED = "expired"


class SignalState(str, enum.Enum):
    DRAFT = "draft"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    QUEUED = "queued"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    RETRIED = "retried"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"
    RECONCILED = "reconciled"


class ConnectorStatus(str, enum.Enum):
    DRAFT = "draft"
    TESTING = "testing"
    PENDING_ACTIVATION = "pending_activation"
    ACTIVE = "active"
    DISABLED = "disabled"


class AssetType(str, enum.Enum):
    SOLAR = "solar"
    WIND = "wind"
    BATTERY = "battery"
    CONSUMER = "consumer"
    GRID_INTERCONNECTION = "grid_interconnection"


# ---------------------------------------------------------------------------
# Tenant, users, roles
# ---------------------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    deployment_mode: Mapped[str] = mapped_column(String(20), default="pooled")  # pooled|siloed|accelerator
    data_residency: Mapped[str] = mapped_column(String(10), default="EU")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    market_area: Mapped[str] = mapped_column(String(20), default="GB")
    units: Mapped[str] = mapped_column(String(10), default="metric")
    retention_years: Mapped[int] = mapped_column(Integer, default=7)
    quotas: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    email: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    roles: Mapped[list[str]] = mapped_column(JSONB, default=list)  # list[Role]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


register_tenant_scoped(User)


# ---------------------------------------------------------------------------
# Portfolio digital twin
# ---------------------------------------------------------------------------


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sites: Mapped[list["Site"]] = relationship(back_populates="portfolio")


register_tenant_scoped(Portfolio)


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    portfolio_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("portfolios.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    grid_connection_point: Mapped[str | None] = mapped_column(String(120), nullable=True)
    market_area: Mapped[str] = mapped_column(String(20), default="GB")
    topology: Mapped[dict] = mapped_column(JSONB, default=dict)

    portfolio: Mapped[Portfolio] = relationship(back_populates="sites")
    assets: Mapped[list["Asset"]] = relationship(back_populates="site")


register_tenant_scoped(Site)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    site_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(30))  # AssetType
    rated_capacity_kw: Mapped[float] = mapped_column(Float)
    ramp_rate_kw_per_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    capability_curve: Mapped[dict] = mapped_column(JSONB, default=dict)
    availability: Mapped[float] = mapped_column(Float, default=1.0)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    control_endpoint_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    site: Mapped[Site] = relationship(back_populates="assets")
    battery: Mapped["Battery | None"] = relationship(back_populates="asset", uselist=False)


register_tenant_scoped(Asset)


class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), unique=True, index=True)
    energy_capacity_kwh: Mapped[float] = mapped_column(Float)
    power_limit_kw: Mapped[float] = mapped_column(Float)
    soc_min_pct: Mapped[float] = mapped_column(Float, default=10.0)
    soc_max_pct: Mapped[float] = mapped_column(Float, default=95.0)
    soc_current_pct: Mapped[float] = mapped_column(Float, default=50.0)
    soh_pct: Mapped[float] = mapped_column(Float, default=100.0)
    round_trip_efficiency: Mapped[float] = mapped_column(Float, default=0.92)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    degradation_cost_per_kwh_cycled: Mapped[float] = mapped_column(Float, default=0.02)
    warranty_cycles_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)

    asset: Mapped[Asset] = relationship(back_populates="battery")


register_tenant_scoped(Battery)


# ---------------------------------------------------------------------------
# Telemetry / forecasts (time-series, Timescale hypertables — see migration)
# ---------------------------------------------------------------------------


class Telemetry(Base):
    """Timescale hypertable partitioned on event_time (migration 0003). The
    partitioning column must be part of every unique/primary key, so the PK
    here is composite (id, event_time) rather than a bare `id`."""

    __tablename__ = "telemetry"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = tenant_fk()
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), index=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)  # e.g. power_kw, soc_pct, irradiance
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    ingest_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    quality: Mapped[str] = mapped_column(String(20), default="good")  # good|stale|bad|estimated
    source: Mapped[str] = mapped_column(String(60), default="edge-simulator")

    __table_args__ = (
        Index("ix_telemetry_asset_metric_time", "asset_id", "metric", "event_time"),
        # idempotent consumers (TRD §9 reliability: "at-least-once + idempotent
        # consumers") — a redelivered stream entry must not create a duplicate row
        UniqueConstraint("tenant_id", "asset_id", "metric", "event_time", name="uq_telemetry_reading"),
    )


register_tenant_scoped(Telemetry)


class Forecast(Base):
    """Timescale hypertable partitioned on valid_time (migration 0003) — see
    the Telemetry docstring for why the PK is composite."""

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = tenant_fk()
    site_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("sites.id"), nullable=True, index=True)
    asset_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=True, index=True)
    variable: Mapped[str] = mapped_column(String(40))  # solar|wind|demand|price|availability
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    quantile: Mapped[float] = mapped_column(Float, default=0.5)  # 0.5 = point/median forecast
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    model_version: Mapped[str] = mapped_column(String(60))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)


register_tenant_scoped(Forecast)


# ---------------------------------------------------------------------------
# Constraints & objective policy
# ---------------------------------------------------------------------------


class Constraint(Base):
    __tablename__ = "constraints"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    scope: Mapped[str] = mapped_column(String(120))  # e.g. asset:<id>, site:<id>, portfolio
    constraint_type: Mapped[str] = mapped_column(String(60))
    expression: Mapped[dict] = mapped_column(JSONB)
    is_hard: Mapped[bool] = mapped_column(Boolean, default=True)
    limit_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(120), default="manual")


register_tenant_scoped(Constraint)


class ObjectivePolicy(Base):
    __tablename__ = "objective_policies"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    version: Mapped[int] = mapped_column(Integer, default=1)
    weights: Mapped[dict] = mapped_column(JSONB)  # {cost, imbalance, degradation, carbon, curtailment, reliability}
    carbon_price_per_tonne: Mapped[float] = mapped_column(Float, default=0.0)
    risk_aversion: Mapped[float] = mapped_column(Float, default=0.2)  # CVaR weight
    combination_method: Mapped[str] = mapped_column(String(60), default="weighted_sum")  # weighted_sum|lexicographic|epsilon_constraint|lexicographic_safety_then_weighted_sum
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


register_tenant_scoped(ObjectivePolicy)


class AutonomyPolicy(Base):
    """FR-GV-001: autonomy modes, server-enforced and effective-dated, at a
    configurable scope. `scope` is a simple string tag — "portfolio" (the
    tenant-wide default), "site:<id>", "asset:<id>", or "asset_type:<type>"
    — with the most specific matching scope winning (see
    reo_common/autonomy.py's resolution order). A tenant/asset cannot move
    out of APPROVAL_REQUIRED into AUTONOMOUS_BOUNDED without a recorded
    safety_case_ref (doc 05 §7: "formal hazard analysis and client-specific
    safety case required before autonomous production") — enforced in
    autonomy.py, not just documented here.
    """

    __tablename__ = "autonomy_policies"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    scope: Mapped[str] = mapped_column(String(120), default="portfolio", index=True)
    mode: Mapped[str] = mapped_column(String(30))  # AutonomyMode
    max_action_risk: Mapped[str] = mapped_column(String(20), default="low")  # ceiling on what AUTONOMOUS_BOUNDED may execute unattended
    safety_case_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    set_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


register_tenant_scoped(AutonomyPolicy)


# ---------------------------------------------------------------------------
# Decisions, actions, approvals
# ---------------------------------------------------------------------------


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("tenant_id", "decision_cycle_id", "version", name="uq_decision_cycle_version"),)

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    portfolio_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("portfolios.id"), index=True)
    decision_cycle_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    trigger: Mapped[str] = mapped_column(String(60), default="scheduled")  # scheduled|event:<name>
    horizon: Mapped[str] = mapped_column(String(10), default="24h")
    autonomy_mode: Mapped[str] = mapped_column(String(30))  # AutonomyMode
    status: Mapped[str] = mapped_column(String(20), default=DecisionStatus.PROPOSED.value)

    trusted_snapshot_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    forecast_bundle_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scenario_set_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    objective_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    constraint_set_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    optimisation_run_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    plan: Mapped[dict] = mapped_column(JSONB)  # chosen actions + objective scores
    alternatives: Mapped[list] = mapped_column(JSONB, default=list)  # Pareto alternatives
    binding_constraints: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[dict] = mapped_column(JSONB, default=dict)  # agent-generated evidence/explanation
    risk_flags: Mapped[list] = mapped_column(JSONB, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list["Action"]] = relationship(back_populates="decision")


register_tenant_scoped(Decision)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    decision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("decisions.id"), index=True)
    asset_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(40))  # charge|discharge|reserve|buy|sell|curtail|demand_response|maintenance_advice
    quantity: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="kW")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    envelope: Mapped[dict] = mapped_column(JSONB, default=dict)  # min/max/ramp/time_window/rate/cumulative
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")  # low|medium|high
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_outcome: Mapped[dict] = mapped_column(JSONB, default=dict)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)

    decision: Mapped[Decision] = relationship(back_populates="actions")


register_tenant_scoped(Action)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    decision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("decisions.id"), index=True)
    action_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("actions.id"), nullable=True, index=True)
    approver_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))  # approved|rejected|modified|held|expired
    modified_bounds: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_second_approver: Mapped[bool] = mapped_column(Boolean, default=False)
    second_approver_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    token: Mapped[str] = mapped_column(String(120), unique=True)  # bound to decision version+approver+bounds+expiry
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


register_tenant_scoped(Approval)


# ---------------------------------------------------------------------------
# Signals & commands (execution layer)
# ---------------------------------------------------------------------------


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    action_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("actions.id"), index=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    decision_version: Mapped[int] = mapped_column(Integer)
    target_asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"))
    command_type: Mapped[str] = mapped_column(String(40))
    setpoint_value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    validity_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    validity_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    safety_limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    approval_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default=SignalState.DRAFT.value)
    state_history: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


register_tenant_scoped(Signal)


class Command(Base):
    """One dispatch attempt of a Signal through the OT command gateway."""

    __tablename__ = "commands"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    signal_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("signals.id"), index=True)
    adapter: Mapped[str] = mapped_column(String(40), default="ot-gateway-sim")
    nonce: Mapped[str] = mapped_column(String(64))
    prepare_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|rejected
    commit_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|committed|failed
    ack_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|acknowledged|timed_out|rejected
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


register_tenant_scoped(Command)


# ---------------------------------------------------------------------------
# Connectors / credentials (Connector Studio, v1.1)
# ---------------------------------------------------------------------------


class CredentialRef(Base):
    __tablename__ = "credential_refs"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    label: Mapped[str] = mapped_column(String(120))
    auth_type: Mapped[str] = mapped_column(String(30))  # oauth2_client_credentials|mtls|api_key|signed_token
    encrypted_payload: Mapped[str] = mapped_column(Text)  # Fernet-encrypted; never returned decrypted via API
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


register_tenant_scoped(CredentialRef)


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(160))
    endpoint_url: Mapped[str] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(10), default="POST")
    headers: Mapped[dict] = mapped_column(JSONB, default=dict)
    credential_ref_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("credential_refs.id"), nullable=True)
    schema_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    retry_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=30)
    approval_policy: Mapped[dict] = mapped_column(JSONB, default=dict)  # e.g. {"requires_maker_checker": true}
    status: Mapped[str] = mapped_column(String(30), default=ConnectorStatus.DRAFT.value)
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    last_test_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


register_tenant_scoped(Connector)


# ---------------------------------------------------------------------------
# Export jobs
# ---------------------------------------------------------------------------


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    requested_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|complete|failed
    object_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


register_tenant_scoped(ExportJob)


class ScenarioRun(Base):
    """A forward-looking what-if solve: the same decision cycle's optimizer
    inputs, re-solved under a named perturbation (cloud cover, wind surge,
    price spike, battery outage, line congestion, demand shock) over the
    full horizon, so the operator sees how the plan and its KPIs would
    change under that variation *before* anything happens for real — this
    is the literal "simulate situation/scenario over a period of time by
    accounting for potential variations in the input conditions" capability
    (hackathon problem 4, solution feature F3), distinct from the
    Simulation Lab's live shock injection into the real-time simulator.
    """

    __tablename__ = "scenario_runs"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    decision_cycle_id: Mapped[str] = mapped_column(String(80), index=True)
    scenario_name: Mapped[str] = mapped_column(String(40))  # BASELINE|CLOUD_COVER|WIND_SURGE|PRICE_SPIKE|BATTERY_OUTAGE|LINE_CONGESTION|DEMAND_SHOCK
    solver_status: Mapped[str] = mapped_column(String(20))
    objective_value: Mapped[float] = mapped_column(Float)
    delta_vs_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_import_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    total_export_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    total_curtailment_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    total_shed_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    binding_constraints: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_scenario_runs_tenant_cycle", "tenant_id", "decision_cycle_id"),)


register_tenant_scoped(ScenarioRun)


class DocumentIntake(Base):
    """A scanned/photographed PDF or image (maintenance notice, storm/weather
    advisory, grid outage notice, inspection report) read via the same
    ModelGateway every specialist agent uses, but with vision — this is the
    "highly heterogeneous multimodal input" capability (hackathon problem 4,
    solution depth D3), which the platform previously only accepted as
    CSV/JSON/XLSX telemetry rows.

    The extraction is evidence only: it never becomes a Constraint on its
    own. A human with `manage:constraints` reviews it and, if it's a real
    asset outage/derate, explicitly promotes it via `constraint_id` — kept
    consistent with the non-negotiable rule that agents produce evidence,
    never commands.
    """

    __tablename__ = "document_intakes"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = tenant_fk()
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    document_type: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text)
    affected_asset_refs: Mapped[list] = mapped_column(JSONB, default=list)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    severity: Mapped[str] = mapped_column(String(20))
    capacity_impact_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    raw_excerpt: Mapped[str] = mapped_column(Text, default="")
    model_provider: Mapped[str] = mapped_column(String(40), default="unknown")
    status: Mapped[str] = mapped_column(String(20), default="extracted")  # extracted|applied|dismissed
    constraint_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("constraints.id"), nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_document_intakes_tenant_created", "tenant_id", "created_at"),)


register_tenant_scoped(DocumentIntake)


# ---------------------------------------------------------------------------
# Audit (tamper-evident, hash-chained; tenant_id nullable for platform events)
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence_pointer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "created_at"),)


# NOTE: AuditEvent is intentionally NOT tenant-scope-filtered at the ORM
# level (auditors/platform admins must be able to query it broadly under
# their own RBAC checks); application code enforces tenant filtering for
# non-platform roles at the API layer instead.
