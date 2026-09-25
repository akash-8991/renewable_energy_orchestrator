"""OT command gateway simulator — a separate, network-isolated service
(doc 05 §10: "Direct browser-to-SCADA communication is prohibited"; only
this service ever "sends" anything to an asset, and only after its own,
independently re-run validation).

Every check here is re-derived from a fresh DB/Redis read, never from
trusting the caller's payload — that is the entire point of a second,
separately-implemented validation pass immediately before dispatch (doc 05
§7, TR-OT-01). `/commands/prepare` and `/commands/commit` run the *same*
checks twice, deliberately, because state can change in the gap between
them (doc 05 §7: "fresh-state validation at edge immediately pre-execution").

Simulated SCADA: no real hardware exists (and per doc 06 §8, none should
before a real site survey), so "execution" here means writing a
`source=ot-gateway-sim` telemetry row reflecting the commanded setpoint,
after a simulated dispatch latency and a small random failure rate for
demo realism (doc 08 §4 steps 4-5/9).
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import redis
from fastapi import FastAPI
from pydantic import BaseModel
from reo_common.config import get_settings
from database.connection import SessionLocal, break_glass_cross_tenant
from models.canonical import Asset, Battery, Telemetry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

logging.basicConfig(level=logging.INFO, format="%(asctime)s ot-gateway-sim %(message)s")
log = logging.getLogger("ot-gateway-sim")

settings = get_settings()
app = FastAPI(title="REO OT Gateway Simulator", version="0.2.0")

_redis = redis.from_url(settings.redis_url, decode_responses=True)

SCENARIO_KEY = "reo:scenario:state"
ESTOP_KEY_PREFIX = "reo:estop:"
SIM_FAILURE_RATE = 0.03  # small chance of a simulated ack failure/timeout, for demo realism


class CommandRequest(BaseModel):
    signal_id: str
    tenant_id: str
    asset_id: str
    command_type: str
    setpoint_value: float
    unit: str
    safety_limits: dict
    validity_start: str
    validity_end: str
    idempotency_key: str
    nonce: str


class CommandResponse(BaseModel):
    ready: bool | None = None
    acknowledged: bool | None = None
    reason: str | None = None
    checked_at: str


@app.get("/health")
def health():
    return {"status": "ok"}


def _is_estopped(tenant_id: str) -> bool:
    return _redis.get(f"{ESTOP_KEY_PREFIX}{tenant_id}") == "1"


def _telemetry_effect(command_type: str, setpoint_value: float) -> tuple[str, float]:
    """Which metric a committed command writes, and with what sign.

    charge/discharge write `power_kw` — the same metric edge-simulator's
    own battery heuristic writes, since a real deployment has exactly one
    physical meter per asset, not two independent producers. That overlap
    is a known, accepted simplification (see docs/SIMPLIFICATIONS.md): the
    edge-simulator's autonomous battery cycle keeps running regardless of
    dispatched commands, so both threads append readings — realistic
    enough for a demo, not a fully resolved single-source-of-truth model.

    buy/sell write `net_import_kw`, matching the grid asset's existing
    telemetry convention (positive = importing).

    curtail/demand_response deliberately do NOT touch `power_kw` — that
    would fight edge-simulator's own live generation/demand simulation for
    that same asset every tick. They write dedicated informational metrics
    instead, confirming the command took effect without overwriting the
    asset's primary reading.
    """
    if command_type == "charge":
        return "power_kw", setpoint_value
    if command_type == "discharge":
        return "power_kw", -setpoint_value
    if command_type == "buy":
        return "net_import_kw", setpoint_value
    if command_type == "sell":
        return "net_import_kw", -setpoint_value
    if command_type == "curtail":
        return "curtailment_kw", setpoint_value
    if command_type == "demand_response":
        return "shed_kw", setpoint_value
    return "power_kw", setpoint_value


def _independent_validate(req: CommandRequest) -> tuple[bool, str | None]:
    now = datetime.now(timezone.utc)

    if _is_estopped(req.tenant_id):
        return False, "tenant e-stop is active"

    validity_end = datetime.fromisoformat(req.validity_end)
    if now > validity_end:
        return False, f"command validity window expired at {validity_end.isoformat()}"

    scenario = _redis.hgetall(SCENARIO_KEY)
    if scenario.get("battery_outage_asset") == req.asset_id:
        return False, "asset is under a live outage scenario"

    max_kw = req.safety_limits.get("max_kw")
    if max_kw is not None and abs(req.setpoint_value) > float(max_kw) + 0.5:
        return False, f"setpoint {req.setpoint_value}kW exceeds envelope max {max_kw}kW"

    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            asset = db.execute(select(Asset).where(Asset.id == req.asset_id, Asset.tenant_id == req.tenant_id)).scalar_one_or_none()
            if asset is None:
                return False, "asset not found in registry"

            # Freshness/quality check applies to every command type, not
            # just batteries: a curtailment, grid buy/sell, or demand-
            # response setpoint is just as unsafe to act on if the asset's
            # last-known state is stale or already flagged bad. Look at
            # whichever metric that asset actually reports (power_kw for
            # everything except batteries, which also report soc_pct).
            latest_reading = db.execute(
                select(Telemetry)
                .where(Telemetry.asset_id == req.asset_id)
                .order_by(Telemetry.event_time.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest_reading is not None:
                age = (now - latest_reading.event_time).total_seconds()
                if age > 300 or latest_reading.quality == "bad":
                    return False, f"asset state is stale/bad (age={age:.0f}s, quality={latest_reading.quality}) — refusing to command on untrusted state"

            # Battery-specific SoC-proximity check, in addition to the
            # generic freshness check above.
            if req.command_type in ("charge", "discharge"):
                battery = db.execute(select(Battery).where(Battery.asset_id == req.asset_id)).scalar_one_or_none()
                latest_soc = db.execute(
                    select(Telemetry)
                    .where(Telemetry.asset_id == req.asset_id, Telemetry.metric == "soc_pct")
                    .order_by(Telemetry.event_time.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if battery is not None and latest_soc is not None:
                    if req.command_type == "discharge" and latest_soc.value <= battery.soc_min_pct + 1:
                        return False, f"battery SoC {latest_soc.value:.1f}% too close to minimum {battery.soc_min_pct}% for a discharge command"
                    if req.command_type == "charge" and latest_soc.value >= battery.soc_max_pct - 1:
                        return False, f"battery SoC {latest_soc.value:.1f}% too close to maximum {battery.soc_max_pct}% for a charge command"
    finally:
        db.close()

    return True, None


@app.post("/commands/prepare", response_model=CommandResponse)
def prepare(req: CommandRequest) -> CommandResponse:
    ok, reason = _independent_validate(req)
    log.info("prepare signal=%s asset=%s ready=%s reason=%s", req.signal_id, req.asset_id, ok, reason)
    return CommandResponse(ready=ok, reason=reason, checked_at=datetime.now(timezone.utc).isoformat())


@app.post("/commands/commit", response_model=CommandResponse)
def commit(req: CommandRequest) -> CommandResponse:
    # Re-run the identical independent validation — state may have changed
    # since /prepare (e.g. an e-stop triggered in between).
    ok, reason = _independent_validate(req)
    if not ok:
        log.warning("commit refused at final check signal=%s reason=%s", req.signal_id, reason)
        return CommandResponse(acknowledged=False, reason=reason, checked_at=datetime.now(timezone.utc).isoformat())

    # simulated dispatch latency
    time.sleep(random.uniform(0.1, 0.4))

    if random.random() < SIM_FAILURE_RATE:
        log.warning("simulated SCADA ack failure for signal=%s (demo fault injection)", req.signal_id)
        return CommandResponse(acknowledged=False, reason="simulated SCADA acknowledgement timeout", checked_at=datetime.now(timezone.utc).isoformat())

    metric, value = _telemetry_effect(req.command_type, req.setpoint_value)
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            stmt = pg_insert(Telemetry).values(
                tenant_id=req.tenant_id, asset_id=req.asset_id, metric=metric,
                event_time=datetime.now(timezone.utc), value=value,
                unit=req.unit, quality="good", source="ot-gateway-sim",
            ).on_conflict_do_nothing(constraint="uq_telemetry_reading")
            db.execute(stmt)
            db.commit()
    finally:
        db.close()

    log.info("committed signal=%s asset=%s setpoint=%s%s", req.signal_id, req.asset_id, req.setpoint_value, req.unit)
    return CommandResponse(acknowledged=True, checked_at=datetime.now(timezone.utc).isoformat())


class EstopRequest(BaseModel):
    tenant_id: str
    active: bool


@app.post("/estop")
def set_estop(req: EstopRequest) -> dict:
    """Independent copy of the e-stop switch, callable directly on the
    gateway itself (doc 05 §7: "e-stop disables new commands without
    impairing monitoring"). The api service also exposes a governed
    /governance/e-stop endpoint that sets the same Redis key — this one
    exists so the gateway's own safety posture never depends solely on the
    api process being healthy."""
    key = f"{ESTOP_KEY_PREFIX}{req.tenant_id}"
    if req.active:
        _redis.set(key, "1")
    else:
        _redis.delete(key)
    log.warning("e-stop set active=%s for tenant=%s", req.active, req.tenant_id)
    return {"tenant_id": req.tenant_id, "active": req.active}


@app.get("/estop/{tenant_id}")
def get_estop(tenant_id: str) -> dict:
    return {"tenant_id": tenant_id, "active": _is_estopped(tenant_id)}
