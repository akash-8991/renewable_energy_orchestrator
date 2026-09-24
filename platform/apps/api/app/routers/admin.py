"""Tenant control plane (BR-PLT-01: "a new tenant can be provisioned and
configured without source-code changes") and per-tenant user management
(FR-PLT-002: platform admins are blocked from tenant operational data by
default; every cross-tenant read here is an explicit, audited break-glass).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from reo_common.audit import append_audit_event
from reo_common.db import break_glass_cross_tenant
from reo_common.models import Role, Tenant, User
from reo_common.security import AuthContext, hash_password
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/admin", tags=["admin"])


class TenantCreateRequest(BaseModel):
    slug: str
    name: str
    deployment_mode: str = "pooled"
    data_residency: str = "EU"
    timezone: str = "Europe/London"
    market_area: str = "GB"


class TenantSummary(BaseModel):
    id: str
    slug: str
    name: str
    deployment_mode: str
    created_at: str


@router.post("/tenants", response_model=TenantSummary)
def create_tenant(
    body: TenantCreateRequest,
    ctx: AuthContext = Depends(require_permission("manage:tenants")),
    db: Session = Depends(db_session),
) -> TenantSummary:
    with break_glass_cross_tenant():
        existing = db.execute(select(Tenant).where(Tenant.slug == body.slug)).scalar_one_or_none()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, f"tenant slug '{body.slug}' already exists")
        tenant = Tenant(
            slug=body.slug, name=body.name, deployment_mode=body.deployment_mode,
            data_residency=body.data_residency, timezone=body.timezone, market_area=body.market_area,
        )
        db.add(tenant)
        db.flush()
        append_audit_event(db, tenant_id=None, actor_id=ctx.user_id, actor_label=ctx.email,
                            event_type="platform.tenant_provisioned", payload={"tenant_id": tenant.id, "slug": tenant.slug})
        db.commit()
        return TenantSummary(id=tenant.id, slug=tenant.slug, name=tenant.name, deployment_mode=tenant.deployment_mode, created_at=tenant.created_at.isoformat())


@router.get("/tenants", response_model=list[TenantSummary])
def list_tenants(ctx: AuthContext = Depends(require_permission("read:platform")), db: Session = Depends(db_session)) -> list[TenantSummary]:
    with break_glass_cross_tenant():
        rows = db.execute(select(Tenant).order_by(Tenant.created_at.desc())).scalars().all()
        append_audit_event(db, tenant_id=None, actor_id=ctx.user_id, actor_label=ctx.email,
                            event_type="platform.break_glass.list_tenants", payload={"count": len(rows)})
        db.commit()
        return [TenantSummary(id=t.id, slug=t.slug, name=t.name, deployment_mode=t.deployment_mode, created_at=t.created_at.isoformat()) for t in rows]


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserCreateRequest(BaseModel):
    email: str
    display_name: str
    password: str
    roles: list[str]

    @field_validator("email")
    @classmethod
    def _validate_email_shape(cls, v: str) -> str:
        # Deliberately a plain shape check, not pydantic's EmailStr: this is
        # an internal admin-provisioning endpoint, and EmailStr's
        # email-validator dependency rejects RFC 2606 reserved TLDs
        # (.test, .example, .invalid, .localhost) outright — exactly the
        # convention this platform's own seed data uses for demo accounts
        # (db/seed.py's *@demo-utility.test users). Deliverability isn't
        # the property that matters for an admin creating an internal
        # account; a well-formed address is.
        if not _EMAIL_RE.match(v):
            raise ValueError("not a well-formed email address")
        return v


class UserSummary(BaseModel):
    id: str
    email: str
    display_name: str
    roles: list[str]
    is_active: bool


VALID_ROLES = {r.value for r in Role} - {Role.PLATFORM_ADMIN.value}  # tenant admins cannot grant platform-level access


@router.post("/users", response_model=UserSummary)
def create_user(
    body: UserCreateRequest, ctx: AuthContext = Depends(require_permission("manage:users")), db: Session = Depends(db_session)
) -> UserSummary:
    invalid = set(body.roles) - VALID_ROLES
    if invalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid roles: {invalid}")
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password exceeds 72 bytes")

    existing = db.execute(select(User).where(User.tenant_id == ctx.tenant_id, User.email == body.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "a user with this email already exists in this tenant")

    user = User(tenant_id=ctx.tenant_id, email=body.email, display_name=body.display_name, hashed_password=hash_password(body.password), roles=body.roles)
    db.add(user)
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="user.created", payload={"user_id": user.id, "email": user.email, "roles": user.roles})
    db.commit()
    return UserSummary(id=user.id, email=user.email, display_name=user.display_name, roles=user.roles, is_active=user.is_active)


@router.get("/users", response_model=list[UserSummary])
def list_users(ctx: AuthContext = Depends(require_permission("manage:users")), db: Session = Depends(db_session)) -> list[UserSummary]:
    rows = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    return [UserSummary(id=u.id, email=u.email, display_name=u.display_name, roles=u.roles, is_active=u.is_active) for u in rows]
