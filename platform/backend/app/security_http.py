"""HTTP-level hardening for the api: security headers, client-IP resolution
and the login throttle."""

from __future__ import annotations

import logging

from fastapi import Request
from reo_common.config import get_settings, is_local_environment
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("api.security")
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        settings = get_settings()
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith(_DOC_PATHS):
            # the api only ever returns JSON/redirects/files — nothing in it should execute in a browser
            headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            headers.setdefault("Cache-Control", "no-store")
        if not is_local_environment(settings):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def client_ip(request: Request) -> str:
    if get_settings().trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoginThrottle:
    """Counts failed logins per account and per client IP in Redis; once a
    limit is reached further attempts get 429 until the window passes, so a
    password can't be guessed by hammering the login endpoint. Fails open
    (with a warning) if Redis is unreachable — a Redis outage should degrade
    protection, not lock everyone out."""

    def __init__(self, redis_client=None):
        self._redis = redis_client

    def _client(self):
        if self._redis is None:
            import redis

            self._redis = redis.from_url(get_settings().redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _keys(tenant_slug: str, email: str, ip: str) -> tuple[str, str]:
        return f"reo:login:fail:acct:{tenant_slug.lower()}:{email.lower()}", f"reo:login:fail:ip:{ip}"

    def retry_after(self, tenant_slug: str, email: str, ip: str) -> int:
        """Seconds the caller must wait, or 0 if they may try."""
        s = get_settings()
        acct, ip_key = self._keys(tenant_slug, email, ip)
        try:
            r = self._client()
            for key, limit in ((acct, s.login_max_failures), (ip_key, s.login_ip_max_failures)):
                count = int(r.get(key) or 0)
                if count >= limit:
                    return max(1, int(r.ttl(key)))
        except Exception:
            log.warning("login throttle unavailable (redis) — failing open", exc_info=True)
        return 0

    def record_failure(self, tenant_slug: str, email: str, ip: str) -> int:
        """Returns the account's failure count after this one."""
        s = get_settings()
        acct, ip_key = self._keys(tenant_slug, email, ip)
        try:
            r = self._client()
            counts = []
            for key in (acct, ip_key):
                n = r.incr(key)
                if n == 1:
                    r.expire(key, s.login_window_seconds)
                counts.append(n)
            return counts[0]
        except Exception:
            log.warning("login throttle unavailable (redis) — failure not recorded", exc_info=True)
            return 0

    def record_success(self, tenant_slug: str, email: str, ip: str) -> None:
        acct, _ = self._keys(tenant_slug, email, ip)
        try:
            self._client().delete(acct)
        except Exception:
            pass


def password_problem(password: str) -> str | None:
    """None if acceptable, else a message safe to show the user."""
    minimum = get_settings().password_min_length
    if len(password) < minimum:
        return f"password must be at least {minimum} characters"
    if len(password.encode("utf-8")) > 72:
        return "password exceeds 72 bytes"
    if len(set(password)) < 4:
        return "password is too repetitive"
    return None
