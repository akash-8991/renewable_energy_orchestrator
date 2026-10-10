"""Connector Studio (FR-CON-001/002/003): authorised humans register a
client-system API endpoint, an auth method, and payload mapping; secrets go
straight to the vault and are never returned in cleartext; activation
requires a *different* human than the one who created it (maker-checker);
a sandbox dry-run is available before activation; disabling is immediate
and requires no counter-approval.
"""

from __future__ import annotations

import hashlib
import json as _json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.config import get_settings
from reo_common.secrets import get_secrets_provider
from reo_common.security import AuthContext
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from guardrails.ssrf import check_outbound_host, check_outbound_url
from models.canonical import Asset, Connector, CredentialRef, Forecast, Tenant
from output.audit import append_audit_event

from ..deps import db_session, require_permission
from ..ingestion.db_source import rows_from_db_table, test_db_connection
from ..ingestion.file_ingest import (
    parse_telemetry_file,
    publish_readings,
    rows_from_file,
)
from ..ingestion.hackathon_dataset import (
    KNOWN_TABLE_KEYS,
    ingest_reference_rows,
    normalize_table_key,
)
from .operations import mark_started_if_idle

router = APIRouter(prefix="/connectors", tags=["connectors"])
settings = get_settings()

# A `database`-kind connector's endpoint_url is a raw Postgres connection
# string, not an HTTP endpoint — check_outbound_url (scheme-restricted to
# http/https) can't validate it, and its host will usually resolve inside
# docker's private address space (blocked by default, same as any other
# private IP a user-typed endpoint might resolve to). This demo's only
# legitimate internal DB target is the reference `source-db` container
# (see infrastructure/docker-compose.yml), so it's the only host allowed —
# a real deployment would instead let a tenant admin maintain this list,
# the same tenant_egress_allowlist mechanism check_outbound_host/_url
# already support for HTTP connectors.
DATABASE_CONNECTOR_ALLOWED_HOSTS = ["source-db"]


def _is_local_data_table_path(kind: str, endpoint_url: str) -> bool:
    return kind == "data_table" and not endpoint_url.startswith(("http://", "https://"))


def _resolve_local_data_path(path_str: str) -> Path:
    """Resolves a data_table connector's non-URL endpoint_url against
    DATA_WATCH_DIR (the same read-only mount the background folder-watcher
    scans) and rejects anything that would escape it (e.g. "../../etc")."""
    watch_dir = Path(settings.data_watch_dir).resolve()
    candidate = (watch_dir / path_str.lstrip("/")).resolve()
    if candidate != watch_dir and watch_dir not in candidate.parents:
        raise ValueError("path must stay inside the platform's watched data folder")
    return candidate


SUPPORTED_DATA_SUFFIXES = (".csv", ".json", ".xlsx", ".xlsm")
MAX_FOLDER_FILES = 200


def _resolve_local_source(path_str: str) -> Path:
    """Like _resolve_local_data_path, but also understands "the whole data
    folder": `.`, `/` or `*` mean the watched folder itself, and so does a path
    whose last segment is `data` that doesn't exist inside the container (the
    host-side path to the folder people naturally paste — it can only ever mean
    the one folder the platform can see). A real sub-folder works too. Whatever
    it resolves to is still confined to the watched folder."""
    watch_dir = Path(settings.data_watch_dir).resolve()
    cleaned = path_str.strip()
    name = Path(cleaned.rstrip("/")).name if cleaned.strip("/") else ""
    if name in ("", ".", "*"):
        return watch_dir
    candidate = _resolve_local_data_path(cleaned)
    if name == "data" and not candidate.exists():
        return watch_dir
    return candidate


def _list_data_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED_DATA_SUFFIXES
    )[:MAX_FOLDER_FILES]


def _missing_local_file_message(path_str: str) -> str:
    """A `data_table` connector's local-path field expects a bare filename
    from the platform's watched data folder (DATA_WATCH_DIR, mounted from
    the host's own `data/` directory) — not that host directory's own
    absolute path, which only means something on the machine typing it, not
    inside this container. Pasting the host path (e.g.
    "/Users/you/project/data") doesn't error out at creation time (it's
    still a syntactically valid path *inside* the watched folder, just one
    that quite reliably doesn't exist there) — it fails here instead, so the
    message actively diagnoses that specific, easy-to-make mistake rather
    than just reporting "not found"."""
    base_message = f"{path_str!r} was not found under the watched data folder"
    if "/" in path_str.strip("/"):
        return (
            f"{base_message}. This field expects a bare filename that already exists directly "
            f"inside the watched data folder (e.g. \"03_renewable_generation.csv\"), or \".\" to "
            f"ingest every supported file in that folder — not a full path, because the host's own "
            f"folder path means nothing inside the container. Copy the file into this platform's "
            f"data/ directory first, then enter just its filename here."
        )
    return base_message


def _validate_database_connector_url(url: str) -> str | None:
    """Returns an error message, or None if the connection string is an
    allowed postgresql:// target."""
    try:
        parsed = make_url(url)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as a validation error
        return f"invalid database connection string: {exc}"
    if not parsed.drivername.startswith("postgresql"):
        return f"only postgresql connection strings are supported (got {parsed.drivername!r})"
    if not parsed.host:
        return "connection string has no host"
    result = check_outbound_host(parsed.host, parsed.port or 5432, tenant_egress_allowlist=DATABASE_CONNECTOR_ALLOWED_HOSTS)
    if not result.allowed:
        return f"host rejected by egress policy: {result.reason}"
    return None


class ConnectorCreateRequest(BaseModel):
    name: str
    kind: Literal["generic", "market_energy_purchase", "scada", "iot", "database", "data_table"] = "generic"
    endpoint_url: str
    method: str = "POST"
    headers: dict = {}
    auth_type: Literal["oauth2_client_credentials", "mtls", "api_key", "signed_token", "none"] = "none"
    credential_payload: dict | None = None  # e.g. {"client_id": "...", "client_secret": "..."} — never stored in plaintext
    schema_mapping: dict = {}
    timeout_seconds: int = 10
    rate_limit_per_min: int = 30
    requires_maker_checker: bool = True


class ConnectorSummary(BaseModel):
    id: str
    name: str
    kind: str
    endpoint_url: str
    method: str
    status: str
    auth_type: str | None
    credential_masked: dict | None
    schema_mapping: dict
    created_by: str | None
    activated_by: str | None
    last_test_result: dict | None
    last_auto_ingest: dict | None = None  # outcome of the background poller's most recent pass over this connector
    created_at: str


AUTO_INGEST_STATUS_KEY = "reo:connector:poll:{connector_id}"  # written by ingestion/connector_poller.py


def _last_auto_ingest(c: Connector) -> dict | None:
    if c.kind not in POLLABLE_CONNECTOR_KINDS:
        return None
    try:
        raw = _fingerprint_store().get(AUTO_INGEST_STATUS_KEY.format(connector_id=c.id))
    except Exception:
        return None
    return _json.loads(raw) if raw else None


def _to_summary(db: Session, c: Connector) -> ConnectorSummary:
    masked = None
    auth_type = None
    if c.credential_ref_id:
        cred = db.execute(select(CredentialRef).where(CredentialRef.id == c.credential_ref_id)).scalar_one_or_none()
        if cred:
            auth_type = cred.auth_type
            masked = get_secrets_provider().masked_summary(cred.encrypted_payload)
    return ConnectorSummary(
        id=c.id, name=c.name, kind=c.kind, endpoint_url=c.endpoint_url, method=c.method, status=c.status,
        auth_type=auth_type, credential_masked=masked, schema_mapping=c.schema_mapping or {},
        created_by=c.created_by, activated_by=c.activated_by,
        last_test_result=c.last_test_result, last_auto_ingest=_last_auto_ingest(c),
        created_at=c.created_at.isoformat(),
    )


@router.get("", response_model=list[ConnectorSummary])
def list_connectors(ctx: AuthContext = Depends(require_permission("manage:connectors")), db: Session = Depends(db_session)) -> list[ConnectorSummary]:
    rows = db.execute(select(Connector).order_by(Connector.created_at.desc())).scalars().all()
    return [_to_summary(db, c) for c in rows]


@router.post("", response_model=ConnectorSummary)
def create_connector(
    body: ConnectorCreateRequest,
    ctx: AuthContext = Depends(require_permission("manage:connectors")),
    db: Session = Depends(db_session),
) -> ConnectorSummary:
    if body.kind == "database":
        db_error = _validate_database_connector_url(body.endpoint_url)
        if db_error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, db_error)
    elif _is_local_data_table_path(body.kind, body.endpoint_url):
        try:
            _resolve_local_data_path(body.endpoint_url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    else:
        ssrf_result = check_outbound_url(body.endpoint_url)
        if not ssrf_result.allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"endpoint rejected by egress policy: {ssrf_result.reason}")

    credential_ref_id = None
    if body.auth_type != "none" and body.credential_payload:
        encrypted = get_secrets_provider().store(body.credential_payload)
        cred = CredentialRef(tenant_id=ctx.tenant_id, label=f"{body.name} credentials", auth_type=body.auth_type, encrypted_payload=encrypted, created_by=ctx.user_id)
        db.add(cred)
        db.flush()
        credential_ref_id = cred.id

    connector = Connector(
        tenant_id=ctx.tenant_id, name=body.name, kind=body.kind, endpoint_url=body.endpoint_url, method=body.method,
        headers=body.headers, credential_ref_id=credential_ref_id, schema_mapping=body.schema_mapping,
        timeout_seconds=body.timeout_seconds, rate_limit_per_min=body.rate_limit_per_min,
        approval_policy={"requires_maker_checker": body.requires_maker_checker},
        status="draft", created_by=ctx.user_id,
    )
    db.add(connector)
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.created", payload={"connector_id": connector.id, "endpoint_url": connector.endpoint_url})
    db.commit()
    return _to_summary(db, connector)


class ConnectorTestResult(BaseModel):
    ssrf_allowed: bool
    ssrf_reason: str | None
    http_reachable: bool | None = None
    http_status: int | None = None
    note: str | None = None
    error: str | None = None


@router.post("/{connector_id}/test", response_model=ConnectorTestResult)
def test_connector(
    connector_id: str, ctx: AuthContext = Depends(require_permission("manage:connectors")), db: Session = Depends(db_session)
) -> ConnectorTestResult:
    connector = db.execute(select(Connector).where(Connector.id == connector_id)).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")

    if connector.kind == "database":
        db_error = _validate_database_connector_url(connector.endpoint_url)
        result = ConnectorTestResult(ssrf_allowed=db_error is None, ssrf_reason=db_error)
        if db_error is None:
            ok, err = test_db_connection(connector.endpoint_url)
            result.http_reachable = ok  # repurposed here as "connected and ran SELECT 1", not an HTTP status
            result.error = err
    elif _is_local_data_table_path(connector.kind, connector.endpoint_url):
        try:
            candidate = _resolve_local_source(connector.endpoint_url)
            exists = candidate.is_file() or candidate.is_dir()
        except ValueError as exc:
            result = ConnectorTestResult(ssrf_allowed=False, ssrf_reason=str(exc))
        else:
            result = ConnectorTestResult(ssrf_allowed=True, ssrf_reason=None)
            result.http_reachable = exists  # repurposed here as "file/folder exists under the watched folder"
            if candidate.is_dir():
                files = _list_data_files(candidate)
                result.note = f"folder with {len(files)} supported file(s)" + (": " + ", ".join(p.name for p in files[:4]) + (" …" if len(files) > 4 else "") if files else "")
                if not files:
                    result.http_reachable = False
                    result.error = "this folder holds no .csv/.json/.xlsx files to ingest"
            elif not exists:
                result.error = _missing_local_file_message(connector.endpoint_url)
    else:
        ssrf_result = check_outbound_url(connector.endpoint_url)
        result = ConnectorTestResult(ssrf_allowed=ssrf_result.allowed, ssrf_reason=ssrf_result.reason)

        if ssrf_result.allowed:
            import httpx
            try:
                with httpx.Client(timeout=connector.timeout_seconds) as client:
                    resp = client.request(connector.method, connector.endpoint_url, headers=connector.headers)
                result.http_reachable = True
                result.http_status = resp.status_code
            except httpx.HTTPError as exc:
                result.http_reachable = False
                result.error = str(exc)

    connector.status = "testing"
    connector.last_test_result = result.model_dump()
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.tested", payload={"connector_id": connector.id, **result.model_dump()})
    db.commit()
    return result


@router.post("/{connector_id}/activate", response_model=ConnectorSummary)
def activate_connector(
    connector_id: str, ctx: AuthContext = Depends(require_permission("activate:connector")), db: Session = Depends(db_session)
) -> ConnectorSummary:
    connector = db.execute(select(Connector).where(Connector.id == connector_id)).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    if connector.status not in ("draft", "testing", "pending_activation"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"connector cannot be activated from status={connector.status}")
    requires_mc = (connector.approval_policy or {}).get("requires_maker_checker", True)
    own_connector = bool(requires_mc and connector.created_by and connector.created_by == ctx.user_id)
    if own_connector and not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "maker-checker: the connector's creator cannot also activate it")

    connector.status = "active"
    connector.activated_by = ctx.user_id
    db.flush()
    # a platform admin (super user) may activate their own connector; the override is recorded, not hidden
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.activated",
                        payload={"connector_id": connector.id, "created_by": connector.created_by,
                                 **({"maker_checker_overridden_by_platform_admin": True} if own_connector else {})})
    db.commit()
    return _to_summary(db, connector)


@router.post("/{connector_id}/disable", response_model=ConnectorSummary)
def disable_connector(
    connector_id: str, ctx: AuthContext = Depends(require_permission("manage:connectors")), db: Session = Depends(db_session)
) -> ConnectorSummary:
    connector = db.execute(select(Connector).where(Connector.id == connector_id)).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    connector.status = "disabled"
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.disabled", payload={"connector_id": connector.id})
    db.commit()
    return _to_summary(db, connector)


# ---------------------------------------------------------------------------
# data_table / database / market_energy_purchase / iot ingestion. `scada`
# remains registration + reachability-test only, by design (doc 05's
# architecture keeps OT dispatch on the independent ot-gateway-sim path
# exclusively, never a registered connector — see ARCHITECTURE.md). The
# other four:
#   - data_table / database: real telemetry/customer rows, gate/auto-start
#     the optimizer (DATA_INGESTION_CONNECTOR_KINDS, routers/operations.py).
#   - market_energy_purchase / iot: real price/telemetry ingestion too (this
#     used to be the production-readiness gap "Connector Studio can register
#     an endpoint but nothing reads from one yet") but deliberately do NOT
#     gate/auto-start — only an explicit database/data_table connection does
#     that, by the same explicit request that narrowed the gate.
# ---------------------------------------------------------------------------


class ConnectorIngestRequest(BaseModel):
    table_name: str | None = None  # database connectors only; data_table ignores this


class ConnectorIngestResponse(BaseModel):
    lineage_id: str | None = None
    rows_queued: int
    detail: dict | None = None  # set when a recognized reference-dataset file/table was routed through hackathon_dataset.py


def _finish_ingest(
    db: Session, ctx: AuthContext, connector: Connector, *, rows_queued: int, lineage_id: str | None, detail: dict | None,
) -> ConnectorIngestResponse:
    if rows_queued:
        tenant = db.execute(select(Tenant).where(Tenant.id == ctx.tenant_id)).scalar_one_or_none()
        if tenant is not None:
            mark_started_if_idle(db, tenant, actor_id=ctx.user_id, actor_label=ctx.email, reason=f"connector:{connector.name}")

    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.ingested",
                        payload={"connector_id": connector.id, "rows_queued": rows_queued, **({"detail": detail} if detail else {})})
    db.commit()
    return ConnectorIngestResponse(lineage_id=lineage_id, rows_queued=rows_queued, detail=detail)


def _finish_non_gating_ingest(
    db: Session, ctx: AuthContext, connector: Connector, *, rows_queued: int, lineage_id: str | None, detail: dict | None,
) -> ConnectorIngestResponse:
    """Same audit/commit as `_finish_ingest`, deliberately without the
    `mark_started_if_idle` call — market_energy_purchase/iot connectors
    ingest real data but must never gate/auto-start the optimizer (only
    database/data_table do, see DATA_INGESTION_CONNECTOR_KINDS)."""
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.ingested",
                        payload={"connector_id": connector.id, "rows_queued": rows_queued, **({"detail": detail} if detail else {})})
    db.commit()
    return ConnectorIngestResponse(lineage_id=lineage_id, rows_queued=rows_queued, detail=detail)


LIVE_MARKET_MODEL_VERSION = "live-market-v1"


def parse_market_price_entries(payload: object) -> list[tuple[datetime, float]] | None:
    """A market_energy_purchase connector's ingest contract: a bare JSON
    array, or `{"prices": [...]}`, of `{"timestamp": ISO8601,
    "price_per_mwh": number}` objects. Returns `None` if `payload` isn't
    even array-shaped (a 400 to the caller); a malformed individual entry is
    silently skipped rather than failing the whole batch (matches every
    other row-level ingestion path in this codebase — a bad row shouldn't
    sink an otherwise-valid feed). Pulled out of `ingest_connector` as a
    pure function so it's testable without a DB, HTTP call, or FastAPI
    request context."""
    entries = payload.get("prices", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return None
    parsed: list[tuple[datetime, float]] = []
    for entry in entries:
        try:
            valid_time = datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00"))
            price = float(entry["price_per_mwh"])
        except (KeyError, ValueError, TypeError):
            continue
        parsed.append((valid_time, price))
    return parsed


POLLABLE_CONNECTOR_KINDS = ("data_table", "database", "market_energy_purchase", "iot")

_fingerprint_redis = None


def _fingerprint_store():
    global _fingerprint_redis
    if _fingerprint_redis is None:
        import redis

        _fingerprint_redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _fingerprint_redis


class _Unchanged(Exception):
    """Raised by _ContentGate when the poller finds nothing new in a source:
    its content is byte-identical to the last successful ingest, or every
    reading in it is at/before what was already ingested."""


def _parse_event_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class _ContentGate:
    """Decides, for the background poller, whether a source has anything
    *new* — so stale data is never re-ingested while the source keeps being
    monitored. Two checks, both kept in Redis per connector:

    - `check` — a SHA-256 fingerprint of the fetched content. Identical to
      the last successful ingest -> nothing new.
    - `only_new` — for readings-shaped sources (generic telemetry files,
      iot feeds): a per-(asset, metric) high-water mark of `event_time`.
      Only readings strictly newer than it are ingested, so an appended
      file contributes just its new rows, and a rewritten file that carries
      nothing newer is treated as no new data.

    Re-ingesting old data is not harmless for every source (the reference-
    dataset path stamps its rows with fresh timestamps, so a repeat would
    append a duplicate series). A manual "Ingest now" bypasses the skip
    (`skip_unchanged=False`) but still records its fingerprint/marks so the
    poller doesn't repeat it. `commit` runs only after a successful ingest,
    so a failed ingest is retried on the next poll."""

    def __init__(self, connector_id: str, skip_unchanged: bool):
        self._key = f"reo:connector:fingerprint:{connector_id}"
        self._mark_key = f"reo:connector:watermark:{connector_id}"
        self._skip = skip_unchanged
        self._pending: str | None = None
        self._pending_marks: dict[str, str] = {}
        self.children: list["_ContentGate"] = []  # per-file gates when a connector points at a folder

    def check(self, material: bytes) -> None:
        digest = hashlib.sha256(material).hexdigest()
        if self._skip and _fingerprint_store().get(self._key) == digest:
            raise _Unchanged()
        self._pending = digest

    def only_new(self, readings: list[dict]) -> list[dict]:
        marks = _fingerprint_store().hgetall(self._mark_key)
        newest: dict[str, datetime] = {}
        fresh: list[dict] = []
        for reading in readings:
            field = f"{reading.get('asset_id')}|{reading.get('metric')}"
            when = _parse_event_time(reading.get("event_time"))
            if when is not None and (field not in newest or when > newest[field]):
                newest[field] = when
            seen = _parse_event_time(marks[field]) if field in marks else None
            if self._skip and when is not None and seen is not None and when <= seen:
                continue  # at/before what this connector already ingested: stale
            fresh.append(reading)
        for field, when in newest.items():
            seen = _parse_event_time(marks[field]) if field in marks else None
            if seen is None or when > seen:
                self._pending_marks[field] = when.isoformat()
        if self._skip and readings and not fresh:
            raise _Unchanged()
        return fresh

    def commit(self) -> None:
        for child in self.children:
            child.commit()
        if self._pending is not None:
            _fingerprint_store().set(self._key, self._pending)
        if self._pending_marks:
            _fingerprint_store().hset(self._mark_key, mapping=self._pending_marks)


@router.post("/{connector_id}/ingest", response_model=ConnectorIngestResponse)
def ingest_connector(
    connector_id: str,
    body: ConnectorIngestRequest = ConnectorIngestRequest(),
    ctx: AuthContext = Depends(require_permission("manage:connectors")),
    db: Session = Depends(db_session),
) -> ConnectorIngestResponse:
    connector = db.execute(select(Connector).where(Connector.id == connector_id)).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    if connector.kind not in POLLABLE_CONNECTOR_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"kind={connector.kind!r} is registration/reachability-test only (scada dispatch always goes through ot-gateway-sim, never a connector, by design)")
    if connector.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"connector must be active to ingest (currently {connector.status}) — test then activate it first")
    return run_connector_ingest(db, ctx, connector, body.table_name)


def run_connector_ingest(
    db: Session, ctx: AuthContext, connector: Connector, table_name: str | None = None, *, skip_unchanged: bool = False,
) -> ConnectorIngestResponse:
    """Fetch and ingest one active connector. Shared by the manual
    `POST /connectors/{id}/ingest` and the background poller
    (ingestion/connector_poller.py), which passes `skip_unchanged=True` and
    gets `rows_queued=0, detail={"status": "no_new_data"}` back when the source
    holds nothing newer than what was last ingested — the poller keeps
    monitoring it but ingests nothing."""
    gate = _ContentGate(connector.id, skip_unchanged)
    try:
        response = _ingest_from_source(db, ctx, connector, table_name, gate)
    except _Unchanged:
        gate.commit()  # remember this content so it isn't re-evaluated every minute either
        return ConnectorIngestResponse(rows_queued=0, detail={"status": "no_new_data"})
    gate.commit()
    return response


def _ingest_from_source(
    db: Session, ctx: AuthContext, connector: Connector, table_name_override: str | None, gate: _ContentGate,
) -> ConnectorIngestResponse:
    if connector.kind in ("market_energy_purchase", "iot"):
        ssrf_result = check_outbound_url(connector.endpoint_url)
        if not ssrf_result.allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"endpoint rejected by egress policy: {ssrf_result.reason}")

        import httpx

        try:
            with httpx.Client(timeout=connector.timeout_seconds) as client:
                resp = client.get(connector.endpoint_url, headers=connector.headers)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not fetch {connector.endpoint_url}: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"endpoint did not return valid JSON: {exc}") from exc

        gate.check(_json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))

        if connector.kind == "market_energy_purchase":
            # Expected shape: a bare array, or {"prices": [...]}, of
            # {"timestamp": ISO8601, "price_per_mwh": number} objects — a
            # small, documented contract (docs/DEPLOYMENT.md A9a) any real
            # market-data provider's response can be adapted to in front of
            # this connector. Written as a real Forecast series (variable=
            # "price") for every grid_interconnection asset, so the next
            # decision cycle picks it up in place of the synthetic price
            # curve for any point this series actually covers.
            parsed_entries = parse_market_price_entries(payload)
            if parsed_entries is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected a JSON array of {timestamp, price_per_mwh}, or {\"prices\": [...]}")

            grid_assets = db.execute(select(Asset).where(Asset.tenant_id == ctx.tenant_id, Asset.asset_type == "grid_interconnection")).scalars().all()
            if not grid_assets:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "no grid_interconnection asset exists for this tenant to attach a price forecast to")

            now = datetime.now(timezone.utc)
            rows_queued = 0
            for valid_time, price in parsed_entries:
                for asset in grid_assets:
                    db.add(Forecast(
                        tenant_id=ctx.tenant_id, site_id=asset.site_id, asset_id=asset.id, variable="price",
                        issue_time=now, valid_time=valid_time, quantile=0.5, value=price, unit="GBP/MWh",
                        model_version=LIVE_MARKET_MODEL_VERSION, is_fallback=False,
                    ))
                    rows_queued += 1
            if rows_queued == 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid entries found (expected timestamp + price_per_mwh on each)")
            db.flush()
            return _finish_non_gating_ingest(
                db, ctx, connector, rows_queued=rows_queued, lineage_id=None,
                detail={"status": "ingested", "table": "live price forecast", "model_version": LIVE_MARKET_MODEL_VERSION},
            )

        # kind == "iot": the identical generic asset_id/metric/event_time/
        # value/unit shape a file upload or data_table connector accepts —
        # any smart-meter/sensor platform's export can be pointed at this
        # once adapted to that shape.
        try:
            readings = parse_telemetry_file("iot-connector.json", _json.dumps(payload).encode("utf-8"))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        if not readings:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid rows found (expected columns: asset_id, metric, event_time, value, unit)")
        readings = gate.only_new(readings)

        from reo_common.events import EventBus

        lineage_id = publish_readings(EventBus(), ctx.tenant_id, readings, lineage_id=f"connector:{connector.id}")
        return _finish_non_gating_ingest(db, ctx, connector, rows_queued=len(readings), lineage_id=lineage_id, detail=None)

    if connector.kind == "database":
        table_name = table_name_override or (connector.schema_mapping or {}).get("table_name")
        if not table_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                 "table_name is required — pass it in the request body, or set schema_mapping.table_name when creating the connector")
        db_error = _validate_database_connector_url(connector.endpoint_url)
        if db_error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, db_error)
        try:
            rows = rows_from_db_table(connector.endpoint_url, table_name)
        except Exception as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not read table {table_name!r}: {exc}") from exc
        gate.check(_json.dumps({"table": table_name, "rows": rows}, sort_keys=True, default=str).encode("utf-8"))

        table_key = normalize_table_key(table_name)
        result = ingest_reference_rows(db, ctx.tenant_id, table_key, rows, source_label=f"connector:{connector.name}:{table_name}")
        return _finish_ingest(db, ctx, connector, rows_queued=result.get("count", 0), lineage_id=result.get("lineage_id"), detail=result)

    # kind == "data_table": endpoint_url is either an http(s) URL (fetched
    # over the network, SSRF-checked) or a path under the platform's watched
    # local data folder (DATA_WATCH_DIR — the same read-only mount the
    # background folder-watcher scans). The local-path form needs no SSRF
    # check: it never leaves the filesystem, unlike an outbound URL a human
    # could point anywhere.
    is_remote = connector.endpoint_url.startswith(("http://", "https://"))
    if is_remote:
        ssrf_result = check_outbound_url(connector.endpoint_url)
        if not ssrf_result.allowed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"endpoint rejected by egress policy: {ssrf_result.reason}")

        filename = Path(connector.endpoint_url.split("?")[0]).name or "connector-data"
        if Path(filename).suffix.lower() not in (".csv", ".json", ".xlsx", ".xlsm"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"endpoint_url must end in .csv/.json/.xlsx (got {filename!r}) — same file-type support as a Document Intake upload",
            )

        import httpx

        try:
            with httpx.Client(timeout=connector.timeout_seconds) as client:
                # Always GET regardless of the connector's own `method` field
                # (default "POST", meaningful for an action-invoking connector)
                # — this is a data fetch, not an action call.
                resp = client.get(connector.endpoint_url, headers=connector.headers)
                resp.raise_for_status()
                content = resp.content
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not fetch {connector.endpoint_url}: {exc}") from exc
    else:
        try:
            candidate = _resolve_local_source(connector.endpoint_url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        if candidate.is_dir():
            return _ingest_folder(db, ctx, connector, candidate, gate)
        if not candidate.is_file():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, _missing_local_file_message(connector.endpoint_url))
        filename = candidate.name
        if candidate.suffix.lower() not in SUPPORTED_DATA_SUFFIXES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"path must end in .csv/.json/.xlsx (got {filename!r})")
        content = candidate.read_bytes()

    gate.check(content)
    rows_queued, lineage_id, detail = _ingest_data_file(db, ctx, connector, filename, content, gate)
    return _finish_ingest(db, ctx, connector, rows_queued=rows_queued, lineage_id=lineage_id, detail=detail)


def _ingest_data_file(
    db: Session, ctx: AuthContext, connector: Connector, filename: str, content: bytes, gate: _ContentGate,
) -> tuple[int, str | None, dict | None]:
    """Parse and ingest one CSV/JSON/XLSX file. A recognised reference-dataset
    file goes through its canonical mapping; anything else must be the generic
    asset_id/metric/event_time/value/unit shape (and, when polling, only
    readings newer than last time are taken). Returns (rows, lineage_id,
    detail)."""
    table_key = normalize_table_key(filename)
    if table_key in KNOWN_TABLE_KEYS:
        rows = rows_from_file(filename, content)
        result = ingest_reference_rows(db, ctx.tenant_id, table_key, rows, source_label=f"connector:{connector.name}")
        return result.get("count", 0), result.get("lineage_id"), result

    try:
        readings = parse_telemetry_file(filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not readings:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid rows found (expected columns: asset_id, metric, event_time, value, unit)")
    readings = gate.only_new(readings)

    from reo_common.events import EventBus

    lineage_id = publish_readings(EventBus(), ctx.tenant_id, readings, lineage_id=f"connector:{connector.id}")
    return len(readings), lineage_id, None


def _ingest_folder(db: Session, ctx: AuthContext, connector: Connector, folder: Path, gate: _ContentGate) -> ConnectorIngestResponse:
    """Ingest every supported file in a folder under the watched data folder.

    Each file is handled on its own: it has its own change check (name + size +
    modification time, so an unchanged multi-megabyte file isn't even re-read on
    every poll) and its own savepoint, so one bad file is reported without
    stopping the others or undoing their work. Files are processed in name
    order."""
    files = _list_data_files(folder)
    watch_dir = Path(settings.data_watch_dir).resolve()
    shown = "." if folder == watch_dir else str(folder.relative_to(watch_dir))
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"no .csv/.json/.xlsx files found in the data folder {shown!r}")

    results: list[dict] = []
    total_rows = 0
    last_lineage: str | None = None
    for path in files:
        file_gate = _ContentGate(f"{connector.id}:{path.name}", gate._skip)  # noqa: SLF001
        stat = path.stat()
        try:
            file_gate.check(f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
        except _Unchanged:
            results.append({"file": path.name, "status": "no_new_data", "rows": 0})
            continue
        try:
            with db.begin_nested():
                rows, lineage_id, detail = _ingest_data_file(db, ctx, connector, path.name, path.read_bytes(), file_gate)
        except _Unchanged:  # generic readings, none newer than what was ingested before
            gate.children.append(file_gate)
            results.append({"file": path.name, "status": "no_new_data", "rows": 0})
            continue
        except HTTPException as exc:
            results.append({"file": path.name, "status": "error", "rows": 0, "error": str(exc.detail)})
            continue
        except Exception as exc:  # noqa: BLE001 — reported per file, never allowed to sink the others
            results.append({"file": path.name, "status": "error", "rows": 0, "error": f"{type(exc).__name__}: {exc}"})
            continue
        gate.children.append(file_gate)
        total_rows += rows
        last_lineage = lineage_id or last_lineage
        entry = {"file": path.name, "status": (detail or {}).get("status", "ingested"), "rows": rows}
        if detail and detail.get("message"):
            entry["message"] = detail["message"]
        results.append(entry)

    errors = [r for r in results if r["status"] == "error"]
    if total_rows == 0:
        if errors:  # nothing came in and something failed: surface it (and retry next poll) without an audit entry per attempt
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no file in the folder could be ingested — " + "; ".join(f"{e['file']}: {e['error']}" for e in errors[:3]))
        if all(r["status"] == "no_new_data" for r in results):
            raise _Unchanged()  # nothing new anywhere in the folder: keep monitoring, ingest nothing
    summary = {
        "status": "folder", "folder": shown, "files": results, "files_seen": len(files),
        "files_ingested": sum(1 for r in results if r["status"] not in ("error", "no_new_data")),
        "files_unchanged": sum(1 for r in results if r["status"] == "no_new_data"), "files_failed": len(errors),
    }
    return _finish_ingest(db, ctx, connector, rows_queued=total_rows, lineage_id=last_lineage, detail=summary)
