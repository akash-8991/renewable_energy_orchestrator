from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from jose import jwt as jose_jwt
from pydantic import BaseModel
from output.audit import append_audit_event
from database.connection import Session, reset_current_tenant, set_current_tenant
from models.canonical import Role, Tenant, User
from reo_common.config import get_settings
from reo_common.platform_settings import get_or_create_platform_settings
from reo_common.security import AuthContext, create_access_token, verify_password
from sqlalchemy import select

from ..deps import db_session, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


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
            subject=user.id, tenant_id=tenant.id, roles=user.roles,
            extra={"email": user.email, "display_name": user.display_name},
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
    email: str
    display_name: str


@router.get("/me", response_model=MeResponse)
def me(ctx: AuthContext = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        user_id=ctx.user_id, tenant_id=ctx.tenant_id, roles=ctx.roles, permissions=sorted(ctx.permissions),
        email=ctx.email, display_name=ctx.display_name,
    )


# ---------------------------------------------------------------------------
# SSO (OIDC authorization-code flow) — production-readiness gap: "authlib
# OIDC client wired but never tested against a real IdP". The bundled
# Keycloak container (infrastructure/docker-compose.yml) is a real, spec-
# compliant IdP, not a mock — this exercises the actual redirect, code
# exchange, JWKS-verified id_token, and account provisioning any real IdP
# integration would need, not just registered settings. Per-tenant opt-in
# via Configuration Studio (`PlatformSettings.sso_enabled`), since not every
# tenant necessarily federates with the same IdP.
# ---------------------------------------------------------------------------

# In-memory CSRF `state` store: fine for this deployment's single api
# replica (same simplification the rest of this codebase makes wherever a
# single-process assumption is explicit — see docs/SIMPLIFICATIONS.md). A
# real multi-replica deployment would move this into Redis, the same store
# already used for the rate limiter/circuit breaker.
_SSO_STATE_TTL_SECONDS = 600
_sso_state_store: dict[str, tuple[str, float]] = {}


def _sso_configured() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_authorize_url_public)


@router.get("/sso/login")
def sso_login(tenant_slug: str) -> RedirectResponse:
    if not _sso_configured():
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "SSO is not configured for this deployment (OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_AUTHORIZE_URL_PUBLIC unset)")
    state = secrets.token_urlsafe(24)
    _sso_state_store[state] = (tenant_slug, time.time() + _SSO_STATE_TTL_SECONDS)
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return RedirectResponse(f"{settings.oidc_authorize_url_public}?{urlencode(params)}")


def _exchange_code_for_claims(code: str) -> dict:
    """Server-to-server: exchanges the authorization code for tokens, then
    verifies the id_token's signature against the IdP's own published JWKS
    (not a bare, unverified decode) before trusting any claim from it."""
    import httpx

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{settings.oidc_issuer}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id, "client_secret": settings.oidc_client_secret,
            },
        )
        resp.raise_for_status()
        token_data = resp.json()
        id_token = token_data["id_token"]

        jwks = client.get(f"{settings.oidc_issuer}/protocol/openid-connect/certs").json()

    # Keycloak's id_token includes an at_hash claim (a hash of the access
    # token, binding the two together) — python-jose verifies it whenever
    # present, and needs the actual access_token to do so; passing it is
    # what turns this into a genuine cross-check rather than a claim we
    # ignore.
    return jose_jwt.decode(
        id_token, jwks, algorithms=["RS256"], audience=settings.oidc_client_id,
        access_token=token_data.get("access_token"),
    )


@router.get("/sso/callback")
def sso_callback(code: str, state: str, db: Session = Depends(db_session)) -> RedirectResponse:
    entry = _sso_state_store.pop(state, None)
    if entry is None or entry[1] < time.time():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired SSO state")
    tenant_slug = entry[0]

    tenant = db.execute(select(Tenant).where(Tenant.slug == tenant_slug)).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown tenant")

    token = set_current_tenant(tenant.id)
    try:
        platform_settings = get_or_create_platform_settings(db, tenant.id)
        if not platform_settings.sso_enabled:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "SSO login is not enabled for this tenant (see Configuration Studio)")

        try:
            claims = _exchange_code_for_claims(code)
        except Exception as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not complete SSO login with the identity provider: {exc}") from exc

        oidc_subject = claims["sub"]
        email = claims.get("email")
        display_name = claims.get("name") or email or oidc_subject

        user = db.execute(select(User).where(User.tenant_id == tenant.id, User.oidc_subject == oidc_subject)).scalar_one_or_none()
        if user is None and email:
            # a local password account logging in via SSO for the first
            # time — link it by email rather than creating a duplicate
            user = db.execute(select(User).where(User.tenant_id == tenant.id, User.email == email)).scalar_one_or_none()
            if user is not None:
                user.oidc_subject = oidc_subject
        if user is None:
            user = User(
                tenant_id=tenant.id, email=email or f"{oidc_subject}@sso.local", display_name=display_name,
                oidc_subject=oidc_subject, roles=[Role.VIEWER.value],
            )
            db.add(user)
            db.flush()
        elif not user.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "this account has been deactivated")

        access_token = create_access_token(
            subject=user.id, tenant_id=tenant.id, roles=user.roles,
            extra={"email": user.email, "display_name": user.display_name},
        )
        append_audit_event(
            db, tenant_id=tenant.id, actor_id=user.id, actor_label=user.email,
            event_type="auth.sso_login", payload={"tenant_slug": tenant_slug, "oidc_subject": oidc_subject},
        )
        db.commit()
    finally:
        reset_current_tenant(token)

    return RedirectResponse(f"{settings.frontend_base_url}/sso-callback?token={access_token}")
