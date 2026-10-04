"""Create the first real tenant and its administrator — the production
alternative to seed.py's demo data.

    docker compose run --rm -e BOOTSTRAP_ADMIN_PASSWORD='<strong password>' api \
        python /app/platform/database/bootstrap.py \
        --tenant-slug acme --tenant-name "Acme Energy" --admin-email ops@acme.example

Idempotent: re-running with the same slug/email changes nothing. The password
is read from BOOTSTRAP_ADMIN_PASSWORD (or prompted for) — never a command-line
argument, which would land in shell history and process listings. Add
--platform-admin to also grant the platform-level role (tenant provisioning,
cross-tenant break-glass). Afterwards sign in and register your sites and
assets under Tenant Administration -> Portfolio registry."""

from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "reo_common"))

from reo_common.config import get_settings  # noqa: E402
from reo_common.security import hash_password  # noqa: E402
from sqlalchemy import select  # noqa: E402

from database.connection import SessionLocal, break_glass_cross_tenant  # noqa: E402
from models.canonical import Role, Tenant, User  # noqa: E402
from output.audit import append_audit_event  # noqa: E402

MIN_PASSWORD_LENGTH = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-name", default="Administrator")
    parser.add_argument("--platform-admin", action="store_true", help="also grant the platform_admin role")
    args = parser.parse_args(argv)

    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass("Administrator password: ")
    if len(password) < MIN_PASSWORD_LENGTH or len(password.encode("utf-8")) > 72 or len(set(password)) < 6:
        print(f"password must be {MIN_PASSWORD_LENGTH}-72 bytes and not repetitive", file=sys.stderr)
        return 2

    settings = get_settings()
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            tenant = db.execute(select(Tenant).where(Tenant.slug == args.tenant_slug)).scalar_one_or_none()
            if tenant is None:
                tenant = Tenant(slug=args.tenant_slug, name=args.tenant_name)
                db.add(tenant)
                db.flush()
                append_audit_event(db, tenant_id=None, actor_id=None, actor_label="bootstrap-cli",
                                    event_type="platform.tenant_provisioned", payload={"tenant_id": tenant.id, "slug": tenant.slug})
                print(f"created tenant {tenant.slug!r} ({tenant.id})")
            else:
                print(f"tenant {tenant.slug!r} already exists")

            roles = [Role.TENANT_ADMIN.value] + ([Role.PLATFORM_ADMIN.value] if args.platform_admin else [])
            user = db.execute(select(User).where(User.tenant_id == tenant.id, User.email == args.admin_email)).scalar_one_or_none()
            if user is None:
                user = User(tenant_id=tenant.id, email=args.admin_email, display_name=args.admin_name,
                            hashed_password=hash_password(password), roles=roles)
                db.add(user)
                db.flush()
                append_audit_event(db, tenant_id=tenant.id, actor_id=None, actor_label="bootstrap-cli",
                                    event_type="user.created", payload={"user_id": user.id, "email": user.email, "roles": roles})
                print(f"created administrator {user.email} with roles {roles}")
            else:
                print(f"administrator {user.email} already exists — left unchanged")
            db.commit()
    finally:
        db.close()
    print(f"environment: {settings.environment}. Sign in with tenant slug {args.tenant_slug!r}, then register your sites and assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
