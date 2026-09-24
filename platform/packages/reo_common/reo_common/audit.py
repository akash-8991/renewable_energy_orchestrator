"""Tamper-evident, hash-chained audit log (FR-AU-001 / TR-AUD-01).

Each AuditEvent row stores sha256(prev_hash + canonical_json(payload)).
Verifying the chain means replaying that computation from the genesis hash
and confirming every stored hash matches — any row edited or deleted after
the fact breaks the chain at that point, which is what makes it
tamper-evident rather than merely tamper-logged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent

GENESIS_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def _compute_hash(prev_hash: str, payload: dict[str, Any], event_type: str, actor_label: str, created_at: str) -> str:
    material = f"{prev_hash}|{event_type}|{actor_label}|{created_at}|{_canonical(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def append_audit_event(
    db: Session,
    *,
    tenant_id: str | None,
    actor_id: str | None,
    actor_label: str,
    event_type: str,
    payload: dict[str, Any],
    evidence_pointer: str | None = None,
) -> AuditEvent:
    # The chain is per-tenant (including a separate chain for tenant_id=NULL
    # platform-level events), not global. A tenant's auditor must be able to
    # verify that tenant's whole chain using only that tenant's own rows —
    # AuditEvent access is tenant-scoped per FR-MT-001 ("...logs tenant-
    # scoped"), so the hash chain has to be independently verifiable within
    # that scope rather than interleaved with other tenants' writes.
    last = db.execute(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id if tenant_id is not None else AuditEvent.tenant_id.is_(None))
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    prev_hash = last.hash if last else GENESIS_HASH

    now = datetime.now(timezone.utc)
    row_hash = _compute_hash(prev_hash, payload, event_type, actor_label, now.isoformat())

    event = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_label=actor_label,
        event_type=event_type,
        payload=payload,
        evidence_pointer=evidence_pointer,
        prev_hash=prev_hash,
        hash=row_hash,
        created_at=now,
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(events: list[AuditEvent]) -> tuple[bool, str | None]:
    """Verify a chronologically ordered list of events. Returns (ok, first_broken_id)."""
    expected_prev = GENESIS_HASH
    for event in events:
        if event.prev_hash != expected_prev:
            return False, event.id
        recomputed = _compute_hash(
            event.prev_hash, event.payload, event.event_type, event.actor_label, event.created_at.isoformat()
        )
        if recomputed != event.hash:
            return False, event.id
        expected_prev = event.hash
    return True, None
