from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.audit import append_audit_event
from reo_common.db import Session, reset_current_tenant, set_current_tenant
from reo_common.models import Tenant, User
from reo_common.security import AuthContext, create_access_token, verify_password
from sqlalchemy import select

from ..deps import db_session, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str
    roles: list[str]
    display_name: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(db_session)) -> LoginResponse:
    tenant = db.execute(select(Tenant).where(Tenant.slug == body.tenant_slug)).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid tenant, email or password")

    token = set_current_tenant(tenant.id)
    try:
        user = db.execute(
            select(User).where(User.tenant_id == tenant.id, User.email == body.email, User.is_active.is_(True))
        ).scalar_one_or_none()
        if user is None or not user.hashed_password or not verify_password(body.password, user.hashed_password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid tenant, email or password")

        access_token = create_access_token(
            subject=user.id, tenant_id=tenant.id, roles=user.roles, extra={"email": user.email}
        )
        append_audit_event(
            db, tenant_id=tenant.id, actor_id=user.id, actor_label=user.email,
            event_type="auth.login", payload={"tenant_slug": body.tenant_slug},
        )
        db.commit()
        return LoginResponse(
            access_token=access_token, tenant_id=tenant.id, roles=user.roles, display_name=user.display_name
        )
    finally:
        reset_current_tenant(token)


class MeResponse(BaseModel):
    user_id: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]


@router.get("/me", response_model=MeResponse)
def me(ctx: AuthContext = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        user_id=ctx.user_id, tenant_id=ctx.tenant_id, roles=ctx.roles, permissions=sorted(ctx.permissions)
    )
