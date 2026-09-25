"""Database engine/session setup plus the tenant-isolation enforcement layer.

Defense in depth for tenant scoping (BR-07 / FR-MT-001): every tenant-scoped
model uses `TenantScopedMixin`. In addition to application code always
filtering by `tenant_id` explicitly, a SQLAlchemy `do_orm_execute` event
listener injects a mandatory `tenant_id == current_tenant_id()` predicate
into every ORM SELECT for a tenant-scoped model, via `with_loader_criteria`.
This means a query that *forgets* to filter by tenant still cannot leak
another tenant's rows — it fails closed (raises) instead, unless the caller
explicitly opts into a logged cross-tenant "break-glass" context.
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncGenerator, Generator
from typing import Any

from sqlalchemy import event, false
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, with_loader_criteria

from reo_common.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Tenant context
# ---------------------------------------------------------------------------

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)
_break_glass: contextvars.ContextVar[bool] = contextvars.ContextVar("break_glass", default=False)


class TenantContextError(RuntimeError):
    """Raised when a tenant-scoped query executes with no tenant context set."""


def set_current_tenant(tenant_id: str | None) -> contextvars.Token:
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant(token: contextvars.Token) -> None:
    _current_tenant_id.reset(token)


def get_current_tenant() -> str | None:
    return _current_tenant_id.get()


class break_glass_cross_tenant:
    """Context manager for the rare, audited platform-admin cross-tenant query.

    Every use of this must be paired with an AuditEvent write by the caller
    (FR-PLT-002: "platform admins blocked from tenant op data by default,
    break-glass logged").
    """

    def __enter__(self):
        self._token = _break_glass.set(True)
        return self

    def __exit__(self, *exc):
        _break_glass.reset(self._token)


# ---------------------------------------------------------------------------
# Declarative base + tenant mixin
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class TenantScopedMixin:
    """Mixin for any table that must never leak across tenants."""

    tenant_id: Any  # concrete column declared per-model (see models.py) to control FK targets


TENANT_SCOPED_REGISTRY: set[type] = set()


def register_tenant_scoped(model_cls: type) -> type:
    TENANT_SCOPED_REGISTRY.add(model_cls)
    return model_cls


@event.listens_for(Session, "do_orm_execute")
def _enforce_tenant_isolation(execute_state) -> None:
    if not execute_state.is_select:
        return
    if _break_glass.get():
        return
    tenant_id = _current_tenant_id.get()
    for model_cls in TENANT_SCOPED_REGISTRY:
        if tenant_id is None:
            # Fail closed: a tenant-scoped select with no tenant context set
            # must return nothing, not everything. `false()` rather than a
            # sentinel string, since tenant_id is a UUID column and a
            # non-UUID literal would raise instead of just matching nothing.
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(model_cls, lambda cls: false())
            )
        else:
            # `tid` MUST be captured as a genuine closure free-variable, not
            # a `lambda cls, tid=tenant_id: ...` default argument. SQLAlchemy
            # caches compiled `with_loader_criteria` lambdas by source code
            # and only re-parameterises tracked *closure* variables on each
            # call — a default-argument value is invisible to that tracking,
            # so the FIRST tenant_id this lambda source ever compiled with
            # gets silently reused as the bound parameter for every later
            # call in the process, regardless of the real current tenant.
            # That produced a real bug here: the second tenant to run a
            # query in the same process got filtered by the *first* tenant's
            # id and saw an empty result for its own data. Binding `tid` as
            # a plain local (a true free variable the lambda closes over)
            # makes SQLAlchemy re-evaluate it correctly on every call — this
            # is the pattern SQLAlchemy's own docs use for dynamic
            # with_loader_criteria values.
            tid = tenant_id
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(model_cls, lambda cls: cls.tenant_id == tid)
            )


# ---------------------------------------------------------------------------
# Engines / sessions
# ---------------------------------------------------------------------------

sync_engine = None
SessionLocal: sessionmaker | None = None
async_engine = None
AsyncSessionLocal: async_sessionmaker | None = None


def init_engines(database_url: str | None = None, async_database_url: str | None = None) -> None:
    global sync_engine, SessionLocal, async_engine, AsyncSessionLocal
    from sqlalchemy import create_engine

    sync_engine = create_engine(database_url or settings.database_url, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False, future=True)

    async_engine = create_async_engine(
        async_database_url or settings.async_database_url, pool_pre_ping=True, future=True
    )
    AsyncSessionLocal = async_sessionmaker(bind=async_engine, autoflush=False, expire_on_commit=False)


init_engines()


def get_db() -> Generator[Session, None, None]:
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    assert AsyncSessionLocal is not None
    async with AsyncSessionLocal() as session:
        yield session
