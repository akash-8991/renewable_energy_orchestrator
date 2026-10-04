"""Production-hardening behaviours found by the end-to-end review: the api
must not trust a token for an account that no longer exists, must throttle
password guessing, must refuse to start with development secrets outside a
local environment, and must serve hardened responses."""

import asyncio
import contextlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent / "policy"))

from app import deps  # noqa: E402
from app.routers.admin import UserCreateRequest, UserUpdateRequest, create_user, update_user  # noqa: E402
from app.security_http import LoginThrottle, password_problem  # noqa: E402
from database.connection import reset_current_tenant, set_current_tenant  # noqa: E402
from models.canonical import AuditEvent, Role, Tenant, User  # noqa: E402
from reo_common.config import Settings, production_config_problems  # noqa: E402
from reo_common.security import AuthContext, hash_password  # noqa: E402


class _Session:
    """Lets code that opens (and closes/commits) its own SessionLocal run
    against the test's rolled-back session."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass

    def commit(self):
        self._real.flush()


@contextlib.contextmanager
def _tenant(tenant_id: str):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


# --- startup guard ----------------------------------------------------------


def test_local_environment_may_use_the_development_defaults():
    assert production_config_problems(Settings(environment="local")) == []


def test_a_real_environment_refuses_the_development_secrets_and_wildcard_cors():
    problems = production_config_problems(Settings(environment="prod"))
    joined = " ".join(problems)
    assert "JWT_SECRET" in joined and "VAULT_MASTER_KEY" in joined and "CORS_ALLOWED_ORIGINS" in joined


def test_a_properly_configured_environment_passes():
    s = Settings(environment="prod", jwt_secret="x" * 48, vault_master_key="k" * 44, cors_allowed_origins="https://reo.example.com")
    assert production_config_problems(s) == []


def test_aws_secrets_provider_does_not_need_the_local_vault_key():
    s = Settings(environment="prod", jwt_secret="x" * 48, secrets_provider="aws", cors_allowed_origins="https://reo.example.com")
    assert production_config_problems(s) == []


# --- password policy --------------------------------------------------------


def test_password_policy():
    assert password_problem("short") is not None
    assert password_problem("aaaaaaaaaaaa") is not None
    assert password_problem("x" * 80) is not None
    assert password_problem("a-Reasonable-Passphrase") is None


# --- login throttle ---------------------------------------------------------


def _ids():
    return f"t-{uuid.uuid4().hex[:8]}", f"{uuid.uuid4().hex[:8]}@example.test", f"10.0.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"


def test_repeated_failures_lock_the_account_out_and_success_clears_it():
    throttle = LoginThrottle()
    tenant, email, ip = _ids()
    assert throttle.retry_after(tenant, email, ip) == 0
    for _ in range(5):
        throttle.record_failure(tenant, email, ip)
    assert throttle.retry_after(tenant, email, ip) > 0

    other = _ids()
    assert throttle.retry_after(tenant, other[1], other[2]) == 0  # a different account/IP is unaffected

    throttle._client().delete(f"reo:login:fail:acct:{tenant.lower()}:{email.lower()}")
    throttle.record_failure(tenant, email, ip)
    throttle.record_success(tenant, email, ip)
    assert throttle.retry_after(tenant, email, ip) == 0


def test_the_login_endpoint_returns_429_after_too_many_failures():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    tenant, email, _ = _ids()
    body = {"tenant_slug": tenant, "email": email, "password": "wrong-password"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429, 429]
    assert client.post("/auth/login", json=body).headers.get("Retry-After")


# --- security headers + readiness --------------------------------------------


def test_responses_carry_security_headers_and_ready_checks_dependencies():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"database": "ok", "redis": "ok"}


# --- accounts are checked on every request -----------------------------------


def _make_user(db, tenant: Tenant, email: str, roles: list[str], active: bool = True) -> User:
    user = User(tenant_id=tenant.id, email=email, display_name=email, hashed_password=hash_password("a-Reasonable-Passphrase"), roles=roles, is_active=active)
    db.add(user)
    db.flush()
    return user


def test_a_token_for_a_deactivated_user_is_no_longer_good(db_session, two_tenants, monkeypatch):
    t1, _ = two_tenants
    monkeypatch.setattr(deps, "SessionLocal", lambda: _Session(db_session))
    with _tenant(t1.id):
        gone = _make_user(db_session, t1, "gone@example.test", ["viewer"], active=False)
        assert deps._load_active_user(gone.id, t1.id) is None


def test_a_token_for_a_deleted_user_or_tenant_is_rejected_not_a_500(monkeypatch, db_session):
    monkeypatch.setattr(deps, "SessionLocal", lambda: _Session(db_session))
    assert deps._load_active_user(str(uuid.uuid4()), str(uuid.uuid4())) is None
    assert deps._load_active_user("not-a-uuid", "also-not") is None


def test_current_roles_come_from_the_database_not_the_token(db_session, two_tenants, monkeypatch):
    t1, _ = two_tenants
    monkeypatch.setattr(deps, "SessionLocal", lambda: _Session(db_session))
    with _tenant(t1.id):
        user = _make_user(db_session, t1, "demoted@example.test", ["viewer"])
        live = deps._load_active_user(user.id, t1.id)
        assert live["roles"] == ["viewer"]


def test_get_current_user_answers_401_for_an_account_that_is_gone(monkeypatch):
    from reo_common.security import create_access_token

    monkeypatch.setattr(deps, "_load_active_user", lambda *_: None)
    token = create_access_token(subject=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()), roles=["tenant_admin"], extra={"email": "x"})
    creds = SimpleNamespace(credentials=token)

    async def run():
        gen = deps.get_current_user(creds)
        await gen.__anext__()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 401


# --- user administration -----------------------------------------------------


def _admin_ctx(user: User) -> AuthContext:
    return AuthContext(user_id=user.id, tenant_id=user.tenant_id, roles=user.roles, email=user.email)


def test_deactivating_and_reactivating_a_user_is_audited(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        admin = _make_user(db_session, t1, "admin1@example.test", [Role.TENANT_ADMIN.value])
        _make_user(db_session, t1, "admin2@example.test", [Role.TENANT_ADMIN.value])
        victim = _make_user(db_session, t1, "leaver@example.test", ["operator"])
        out = update_user(victim.id, UserUpdateRequest(is_active=False), ctx=_admin_ctx(admin), db=db_session)
        assert out.is_active is False
        assert update_user(victim.id, UserUpdateRequest(is_active=True), ctx=_admin_ctx(admin), db=db_session).is_active is True
        events = db_session.execute(select(AuditEvent.event_type).where(AuditEvent.tenant_id == t1.id)).scalars().all()
        assert "user.deactivated" in events and "user.updated" in events


def test_a_tenant_cannot_lose_its_last_administrator_or_lock_itself_out(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        only = _make_user(db_session, t1, "only-admin@example.test", [Role.TENANT_ADMIN.value])
        with pytest.raises(HTTPException) as exc:
            update_user(only.id, UserUpdateRequest(is_active=False), ctx=_admin_ctx(only), db=db_session)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            update_user(only.id, UserUpdateRequest(roles=["viewer"]), ctx=_admin_ctx(only), db=db_session)
        assert exc.value.status_code == 409

        second = _make_user(db_session, t1, "second-admin@example.test", [Role.TENANT_ADMIN.value])
        with pytest.raises(HTTPException) as exc:  # even with a second admin, not your own account
            update_user(second.id, UserUpdateRequest(is_active=False), ctx=_admin_ctx(second), db=db_session)
        assert "own account" in exc.value.detail


def test_role_changes_validate_and_password_reset_enforces_the_policy(db_session, two_tenants):
    t1, _ = two_tenants
    with _tenant(t1.id):
        admin = _make_user(db_session, t1, "a@example.test", [Role.TENANT_ADMIN.value])
        user = _make_user(db_session, t1, "u@example.test", ["viewer"])
        with pytest.raises(HTTPException):
            update_user(user.id, UserUpdateRequest(roles=["platform_admin"]), ctx=_admin_ctx(admin), db=db_session)
        with pytest.raises(HTTPException):
            update_user(user.id, UserUpdateRequest(password="short"), ctx=_admin_ctx(admin), db=db_session)
        assert update_user(user.id, UserUpdateRequest(roles=["operator"], password="a-Brand-New-Passphrase"), ctx=_admin_ctx(admin), db=db_session).roles == ["operator"]
        with pytest.raises(HTTPException) as exc:
            create_user(UserCreateRequest(email="weak@example.test", display_name="w", password="short", roles=["viewer"]), ctx=_admin_ctx(admin), db=db_session)
        assert exc.value.status_code == 400


# --- housekeeping, uploads, heartbeat, streams ---------------------------------


def test_observability_pruning_keeps_recent_rows_and_the_ledger(db_session, two_tenants, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import housekeeping
    from models.canonical import ScenarioRun

    t1, _ = two_tenants
    monkeypatch.setattr(housekeeping, "SessionLocal", lambda: _Session(db_session))
    with _tenant(t1.id):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        for created in (old, datetime.now(timezone.utc)):
            db_session.add(ScenarioRun(tenant_id=t1.id, decision_cycle_id="c", scenario_name="BASELINE", solver_status="OPTIMAL",
                                       objective_value=1.0, created_at=created))
        db_session.flush()
        housekeeping.prune_observability(90)
        remaining = db_session.execute(select(ScenarioRun).where(ScenarioRun.tenant_id == t1.id)).scalars().all()
        assert len(remaining) == 1 and remaining[0].created_at > old


def test_uploads_are_cut_off_at_the_limit_without_reading_everything():
    from app.routers.ingestion import _read_limited

    class Fake:
        def __init__(self, size):
            self.left, self.served = size, 0

        async def read(self, n):
            chunk = min(n, self.left)
            self.left -= chunk
            self.served += chunk
            return b"x" * chunk

    ok = asyncio.run(_read_limited(Fake(3 * 1024 * 1024), 5 * 1024 * 1024, "too big"))
    assert len(ok) == 3 * 1024 * 1024

    big = Fake(50 * 1024 * 1024)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_limited(big, 5 * 1024 * 1024, "too big"))
    assert exc.value.status_code == 413
    assert big.served <= 6 * 1024 * 1024  # stopped right after crossing the limit, not after buffering 50MB


def test_a_worker_heartbeat_is_visible_and_expires():
    from reo_common import heartbeat

    name = f"test-{uuid.uuid4().hex[:8]}"
    assert heartbeat.is_alive(name) is False
    heartbeat.beat(name)
    assert heartbeat.is_alive(name) is True
    heartbeat._redis().delete(heartbeat.KEY.format(name=name))


def test_streams_are_capped_so_they_cannot_grow_without_bound(monkeypatch):
    from reo_common.events import CloudEvent, EventBus, STREAM_TELEMETRY

    bus = EventBus()
    seen = {}
    monkeypatch.setattr(bus._redis, "xadd", lambda stream, fields, **kw: seen.update(kw) or "1-0")
    bus.publish(STREAM_TELEMETRY, CloudEvent(type="t", source="s", tenant_id=None, data={}))
    assert seen["maxlen"] == 500_000 and seen["approximate"] is True
    bus.publish("reo.something.else", CloudEvent(type="t", source="s", tenant_id=None, data={}))
    assert seen["maxlen"] == 20_000


# --- bootstrap + seed guard --------------------------------------------------------


def test_the_demo_seed_refuses_to_run_in_a_real_environment(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent.parent / "database"))
    import importlib

    seed = importlib.import_module("seed")
    monkeypatch.setattr(seed.settings, "environment", "prod")
    monkeypatch.delenv("ALLOW_DEMO_SEED", raising=False)
    with pytest.raises(SystemExit) as exc:
        seed.main()
    assert exc.value.code == 1


def test_bootstrap_creates_a_tenant_and_admin_once(db_session, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent.parent / "database"))
    import importlib

    bootstrap = importlib.import_module("bootstrap")
    monkeypatch.setattr(bootstrap, "SessionLocal", lambda: _Session(db_session))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "a-Very-Strong-Passphrase-1")
    slug = f"boot-{uuid.uuid4().hex[:8]}"
    args = ["--tenant-slug", slug, "--tenant-name", "Boot Co", "--admin-email", "root@boot.example"]
    assert bootstrap.main(args) == 0
    assert bootstrap.main(args) == 0  # idempotent
    from database.connection import break_glass_cross_tenant

    with break_glass_cross_tenant():
        tenant = db_session.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one()
        users = db_session.execute(select(User).where(User.tenant_id == tenant.id)).scalars().all()
    assert [u.roles for u in users] == [["tenant_admin"]]

    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "weak")
    assert bootstrap.main(["--tenant-slug", "x", "--tenant-name", "x", "--admin-email", "a@b.example"]) == 2
