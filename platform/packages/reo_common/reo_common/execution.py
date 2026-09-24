"""Signal creation and OT command dispatch client (FR-CM-001/002/003,
doc 05 §7 safety architecture).

The actual safety-critical logic — independent re-validation immediately
pre-dispatch, e-stop enforcement, simulated SCADA acknowledgement — lives
in the separate `ot-gateway-sim` service (its own process, its own trust
boundary; see docs/ARCHITECTURE.md). This module is the *client* side:
building a correctly-shaped Signal, and the prepare/commit HTTP round-trip
that never assumes success without a response.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .audit import append_audit_event
from .models import Action, Command, Decision, Signal

log = logging.getLogger("reo_common.execution")

DISPATCHABLE_ACTION_TYPES = {"charge", "discharge"}  # curtail/demand_response/maintenance_advice are advisory-only in this build (see SIMPLIFICATIONS.md)


def build_signal_for_action(db: Session, decision: Decision, action: Action) -> Signal | None:
    if action.action_type not in DISPATCHABLE_ACTION_TYPES:
        return None
    idempotency_key = f"{decision.id}:{action.id}"
    signal = Signal(
        tenant_id=decision.tenant_id,
        action_id=action.id,
        correlation_id=decision.decision_cycle_id,
        decision_version=decision.version,
        target_asset_id=action.asset_id,
        command_type=action.action_type,
        setpoint_value=action.quantity,
        unit=action.unit,
        validity_start=action.start_time,
        validity_end=action.end_time,
        safety_limits={**action.envelope, "expiry": action.expiry.isoformat() if action.expiry else None},
        idempotency_key=idempotency_key,
        state="draft",
        state_history=[{"state": "draft", "at": datetime.now(timezone.utc).isoformat()}],
    )
    db.add(signal)
    db.flush()
    return signal


def _transition(signal: Signal, new_state: str, note: str | None = None) -> None:
    signal.state = new_state
    signal.state_history = [*signal.state_history, {"state": new_state, "at": datetime.now(timezone.utc).isoformat(), "note": note}]


def dispatch_signal(db: Session, signal: Signal, *, ot_gateway_base_url: str, actor_label: str) -> Command:
    """Prepare -> commit against the OT gateway. Never assumes success: a
    network error or a non-ready prepare response results in a rejected/
    timed_out Signal, not a silently-assumed-successful one (FR-CM-003:
    "on timeout/rejection... cancel/escalate/fallback")."""
    nonce = secrets.token_hex(16)
    command = Command(
        tenant_id=signal.tenant_id, signal_id=signal.id, adapter="ot-gateway-sim", nonce=nonce,
        prepare_status="pending", commit_status="pending", ack_status="pending",
    )
    db.add(command)
    db.flush()

    payload = {
        "signal_id": signal.id,
        "tenant_id": signal.tenant_id,
        "asset_id": signal.target_asset_id,
        "command_type": signal.command_type,
        "setpoint_value": signal.setpoint_value,
        "unit": signal.unit,
        "safety_limits": signal.safety_limits,
        "validity_start": signal.validity_start.isoformat(),
        "validity_end": signal.validity_end.isoformat(),
        "idempotency_key": signal.idempotency_key,
        "nonce": nonce,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            prepare_resp = client.post(f"{ot_gateway_base_url}/commands/prepare", json=payload)
            prepare_resp.raise_for_status()
            prepare_data = prepare_resp.json()
    except httpx.HTTPError as exc:
        log.warning("OT gateway prepare call failed for signal %s: %s", signal.id, exc)
        command.prepare_status = "rejected"
        command.result = {"error": str(exc)}
        _transition(signal, "timed_out", f"prepare call failed: {exc}")
        append_audit_event(db, tenant_id=signal.tenant_id, actor_id=None, actor_label=actor_label,
                            event_type="signal.dispatch.prepare_failed", payload={"signal_id": signal.id, "error": str(exc)})
        return command

    command.prepare_status = "ready" if prepare_data.get("ready") else "rejected"
    if not prepare_data.get("ready"):
        command.result = prepare_data
        _transition(signal, "rejected", prepare_data.get("reason"))
        append_audit_event(db, tenant_id=signal.tenant_id, actor_id=None, actor_label=actor_label,
                            event_type="signal.dispatch.prepare_rejected", payload={"signal_id": signal.id, **prepare_data})
        return command

    _transition(signal, "sent")
    try:
        with httpx.Client(timeout=10.0) as client:
            commit_resp = client.post(f"{ot_gateway_base_url}/commands/commit", json=payload)
            commit_resp.raise_for_status()
            commit_data = commit_resp.json()
    except httpx.HTTPError as exc:
        log.warning("OT gateway commit call failed for signal %s: %s", signal.id, exc)
        command.commit_status = "failed"
        command.result = {"error": str(exc)}
        _transition(signal, "timed_out", f"commit call failed: {exc}")
        append_audit_event(db, tenant_id=signal.tenant_id, actor_id=None, actor_label=actor_label,
                            event_type="signal.dispatch.commit_failed", payload={"signal_id": signal.id, "error": str(exc)})
        return command

    command.commit_status = "committed" if commit_data.get("acknowledged") else "failed"
    command.ack_status = "acknowledged" if commit_data.get("acknowledged") else "rejected"
    command.result = commit_data
    command.completed_at = datetime.now(timezone.utc)
    _transition(signal, "acknowledged" if commit_data.get("acknowledged") else "rejected", commit_data.get("reason"))

    append_audit_event(
        db, tenant_id=signal.tenant_id, actor_id=None, actor_label=actor_label,
        event_type="signal.dispatch.result", payload={"signal_id": signal.id, "acknowledged": commit_data.get("acknowledged"), "result": commit_data},
    )
    return command
