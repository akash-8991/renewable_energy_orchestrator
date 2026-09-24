from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from reo_common.db import Session, get_db, reset_current_tenant, set_current_tenant
from reo_common.security import AuthContext, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Generator[AuthContext, None, None]:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    ctx = AuthContext(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        roles=payload.get("roles", []),
        email=payload.get("email", ""),
    )
    token = set_current_tenant(ctx.tenant_id)
    try:
        yield ctx
    finally:
        reset_current_tenant(token)


def require_permission(permission: str):
    def _dep(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not ctx.has_permission(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {permission}")
        return ctx

    return _dep


def require_role(*roles: str):
    def _dep(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not any(ctx.has_role(r) for r in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires one of roles: {roles}")
        return ctx

    return _dep


def db_session() -> Generator[Session, None, None]:
    yield from get_db()
