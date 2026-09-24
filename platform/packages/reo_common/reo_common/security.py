"""JWT issuing/verification, password hashing, and RBAC permission checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from .config import get_settings
from .models import Role

settings = get_settings()

# Passlib's bcrypt wrapper is unmaintained and breaks against bcrypt>=4.1
# (https://github.com/pyca/bcrypt/issues/684), so we call the bcrypt
# package directly — it's a maintained, minimal dependency for this.
_BCRYPT_MAX_BYTES = 72  # bcrypt silently truncates beyond this; reject instead of truncating


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raise ValueError(f"password exceeds bcrypt's {_BCRYPT_MAX_BYTES}-byte limit")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(*, subject: str, tenant_id: str, roles: list[str], extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "roles": roles,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
        **(extra or {}),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("invalid or expired token") from exc


# ---------------------------------------------------------------------------
# RBAC — FRD §4 permission surface
# ---------------------------------------------------------------------------

# Coarse-grained permission catalogue. Each role maps to the set of
# permissions it holds; endpoints declare the permission(s) they require.
PERMISSIONS: dict[str, set[str]] = {
    Role.VIEWER: {"read:dashboard", "read:decisions", "read:audit"},
    Role.OPERATOR: {
        "read:dashboard", "read:decisions", "read:audit",
        "approve:assigned", "acknowledge:signal", "override:bounded",
    },
    Role.SENIOR_OPERATOR: {
        "read:dashboard", "read:decisions", "read:audit",
        "approve:assigned", "approve:four_eyes", "acknowledge:signal",
        "override:bounded", "pause:operations", "e_stop:trigger",
    },
    Role.PORTFOLIO_MANAGER: {
        "read:dashboard", "read:decisions", "read:audit",
        "manage:objective_policy", "manage:scenarios", "read:economics", "ingest:files",
    },
    Role.OT_ADMIN: {
        "read:dashboard", "read:decisions", "read:audit",
        "manage:adapters", "manage:command_envelopes", "read:control_readiness",
    },
    Role.MODEL_ADMIN: {
        "read:dashboard", "read:decisions", "read:audit",
        "manage:model_registry", "manage:model_eval", "deploy:model",
    },
    Role.TENANT_ADMIN: {
        "read:dashboard", "read:decisions", "read:audit",
        "manage:users", "manage:settings", "manage:connectors",
        "manage:policies", "activate:connector", "ingest:files",
    },
    Role.AUDITOR_DPO: {
        "read:dashboard", "read:decisions", "read:audit",
        "export:evidence", "read:privacy", "manage:dsr",
    },
    Role.PLATFORM_ADMIN: {
        "read:platform", "manage:tenants", "manage:platform_config",
        "read:platform_health", "break_glass:cross_tenant",
    },
}


class AuthContext:
    def __init__(self, user_id: str, tenant_id: str, roles: list[str], email: str):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.roles = roles
        self.email = email

    @property
    def permissions(self) -> set[str]:
        perms: set[str] = set()
        for role in self.roles:
            perms |= PERMISSIONS.get(role, set())
        return perms

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.has_permission(permission):
            raise PermissionError(f"missing permission: {permission}")

    def has_role(self, role: str) -> bool:
        return role in self.roles
