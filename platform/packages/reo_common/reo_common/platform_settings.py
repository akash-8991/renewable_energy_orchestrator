"""Shared `PlatformSettings` helpers (Configuration Studio's backing store).
Lives in `reo_common`, not `backend/app/`, because it's used by every
process that constructs a `ModelGateway` — `agent-worker` and the API
process alike — the same reason `model_gateway.py` itself already reaches
into the shared `guardrails`/`models`/`database` top-level packages rather
than anything under `backend/`."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .config import get_settings
from .model_gateway import ModelGateway

log = logging.getLogger("reo.platform_settings")


def get_or_create_platform_settings(db: Session, tenant_id: str):
    from sqlalchemy import select

    from models.canonical import PlatformSettings

    row = db.execute(select(PlatformSettings).where(PlatformSettings.tenant_id == tenant_id)).scalar_one_or_none()
    if row is None:
        defaults = get_settings()
        row = PlatformSettings(
            tenant_id=tenant_id,
            gateway_circuit_breaker_enabled=defaults.model_gateway_circuit_breaker_enabled_default,
            gateway_timeout_seconds=defaults.model_gateway_timeout_seconds_default,
            gateway_failure_threshold=defaults.model_gateway_circuit_failure_threshold_default,
            gateway_cooldown_seconds=defaults.model_gateway_circuit_cooldown_seconds_default,
        )
        db.add(row)
        db.flush()
    return row


def attach_circuit_breaker(gateway: ModelGateway, db: Session, tenant_id: str) -> None:
    """Best-effort: reads the tenant's PlatformSettings row and, if enabled,
    attaches a configured `ModelCallCircuitBreaker` to `gateway` before any
    call is made this cycle. Never raises — a config-read or Redis-
    construction problem degrades to "no breaker for this cycle", the same
    fail-open policy the breaker's own per-call methods already apply."""
    try:
        row = get_or_create_platform_settings(db, tenant_id)
        if not row.gateway_circuit_breaker_enabled:
            return
        import redis as redis_lib

        from guardrails.circuit_breaker import ModelCallCircuitBreaker

        client = redis_lib.from_url(get_settings().redis_url, decode_responses=True)
        gateway.circuit_breaker = ModelCallCircuitBreaker(
            client, tenant_id=tenant_id, timeout_seconds=row.gateway_timeout_seconds,
            failure_threshold=row.gateway_failure_threshold, cooldown_seconds=row.gateway_cooldown_seconds,
        )
    except Exception:
        log.warning("could not attach model-call circuit breaker for tenant=%s — proceeding without one", tenant_id, exc_info=True)
