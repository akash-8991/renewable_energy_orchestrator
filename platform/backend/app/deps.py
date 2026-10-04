from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from database.connection import Session, SessionLocal, get_db, reset_current_tenant, set_current_tenant
from models.canonical import Tenant, User
from reo_common.security import AuthContext, decode_access_token
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

bearer_scheme = HTTPBearer(auto_error=False)


def _load_active_user(user_id: str, tenant_id: str) -> dict | None:
    """The user as the database sees them *now*, or None if they no longer
    exist / are deactivated / their tenant is gone. A signed token only proves
    who someone was when it was issued; this is what makes deactivating a user
    or changing their roles take effect on the very next request instead of
    when the token expires."""
    db = SessionLocal()
    token = set_current_tenant(tenant_id)
    try:
        if db.get(Tenant, tenant_id) is None:
            return None
        user = db.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id, User.is_active.is_(True))
        ).scalar_one_or_none()
        if user is None:
            return None
        return {"roles": list(user.roles or []), "email": user.email, "display_name": user.display_name}
    except Exception:
        return None  # e.g. a malformed id in a forged-but-signed token: treat as not-a-user, never a 500
    finally:
        reset_current_tenant(token)
        db.close()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AsyncGenerator[AuthContext, None]:
    # This MUST be an async generator, not a sync one: FastAPI dispatches
    # each half of a sync generator dependency (before/after yield) as a
    # separate threadpool call, which can land on different OS threads —
    # and a contextvars.Token created in one thread's Context cannot be
    # reset() from another, which is exactly the tenant-context token this
    # function creates. An async generator runs entirely on the event loop
    # in one Context, so set/reset stay paired correctly.
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    live = await run_in_threadpool(_load_active_user, payload.get("sub", ""), payload.get("tenant_id", ""))
    if live is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account no longer exists or has been deactivated")

    ctx = AuthContext(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        roles=live["roles"],  # current roles, not whatever the token was minted with
        email=live["email"],
        display_name=live["display_name"],
    )
    token = set_current_tenant(ctx.tenant_id)
    try:
        yield ctx
    finally:
        reset_current_tenant(token)


def require_permission(permission: str):
    async def _dep(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not ctx.has_permission(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {permission}")
        return ctx

    return _dep


def require_role(*roles: str):
    async def _dep(ctx: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not any(ctx.has_role(r) for r in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires one of roles: {roles}")
        return ctx

    return _dep


def db_session() -> Generator[Session, None, None]:
    yield from get_db()
