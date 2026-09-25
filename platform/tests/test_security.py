"""RBAC: PLATFORM_ADMIN is a true superuser (every permission that exists
anywhere, not just its own five), by explicit request — every other role
only gets what its own PERMISSIONS entry lists."""

from reo_common.security import _ALL_PERMISSIONS, AuthContext, PERMISSIONS, Role


def _ctx(*roles: str) -> AuthContext:
    return AuthContext(user_id="u1", tenant_id="t1", roles=list(roles), email="test@example.com")


def test_platform_admin_has_every_declared_permission():
    ctx = _ctx(Role.PLATFORM_ADMIN.value)
    assert ctx.permissions == set(_ALL_PERMISSIONS)
    assert len(_ALL_PERMISSIONS) > len(PERMISSIONS[Role.PLATFORM_ADMIN])


def test_platform_admin_passes_a_permission_check_its_own_role_never_declared():
    # approve:assigned belongs to OPERATOR/SENIOR_OPERATOR only — not listed
    # under PLATFORM_ADMIN in PERMISSIONS, but the superuser override must
    # still grant it.
    assert "approve:assigned" not in PERMISSIONS[Role.PLATFORM_ADMIN]
    ctx = _ctx(Role.PLATFORM_ADMIN.value)
    assert ctx.has_permission("approve:assigned")


def test_platform_admin_stays_superuser_even_combined_with_other_roles():
    ctx = _ctx(Role.PLATFORM_ADMIN.value, Role.VIEWER.value)
    assert ctx.permissions == set(_ALL_PERMISSIONS)


def test_non_platform_admin_roles_are_unaffected():
    ctx = _ctx(Role.VIEWER.value)
    assert ctx.permissions == PERMISSIONS[Role.VIEWER]
    assert not ctx.has_permission("manage:tenants")


def test_a_new_permission_added_to_any_role_is_automatically_covered():
    # _ALL_PERMISSIONS is derived from PERMISSIONS at import time, not a
    # separately maintained list — this is the property that makes it
    # actually stay in sync as the permission surface grows.
    assert _ALL_PERMISSIONS == frozenset(p for perms in PERMISSIONS.values() for p in perms)
