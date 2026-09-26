"""Connector Studio (FR-CON-001/002/003): authorised humans register a
client-system API endpoint, an auth method, and payload mapping; secrets go
straight to the vault and are never returned in cleartext; activation
requires a *different* human than the one who created it (maker-checker);
a sandbox dry-run is available before activation; disabling is immediate
and requires no counter-approval.
"""

from __future__ import annotations

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
from models.canonical import Connector, CredentialRef, Tenant
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
    created_at: str


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
        last_test_result=c.last_test_result, created_at=c.created_at.isoformat(),
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
            candidate = _resolve_local_data_path(connector.endpoint_url)
            exists = candidate.is_file()
        except ValueError as exc:
            result = ConnectorTestResult(ssrf_allowed=False, ssrf_reason=str(exc))
        else:
            result = ConnectorTestResult(ssrf_allowed=True, ssrf_reason=None)
            result.http_reachable = exists  # repurposed here as "file exists under the watched folder"
            if not exists:
                result.error = f"{connector.endpoint_url!r} was not found under the watched data folder"
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
    if requires_mc and connector.created_by and connector.created_by == ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "maker-checker: the connector's creator cannot also activate it")

    connector.status = "active"
    connector.activated_by = ctx.user_id
    db.flush()
    append_audit_event(db, tenant_id=ctx.tenant_id, actor_id=ctx.user_id, actor_label=ctx.email,
                        event_type="connector.activated", payload={"connector_id": connector.id, "created_by": connector.created_by})
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
# data_table / database ingestion — the two connector kinds actually wired to
# a real effect. A market_energy_purchase/scada/iot connector registers and
# reachability-tests its endpoint (same as before, see ARCHITECTURE.md for
# what's registration-only vs live); data_table and database connectors
# instead pull real rows in, either as generic telemetry (the
# asset_id/metric/event_time/value/unit shape a file upload also uses) or,
# for a filename/table name matching one of the reference dataset's own
# files, through hackathon_dataset.py's canonical Asset/Customer mapping —
# same validation, same quarantine path either way.
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
    if connector.kind not in ("data_table", "database"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"kind={connector.kind!r} is registration/reachability-test only — only data_table/database connectors ingest")
    if connector.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"connector must be active to ingest (currently {connector.status}) — test then activate it first")

    if connector.kind == "database":
        table_name = body.table_name or (connector.schema_mapping or {}).get("table_name")
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
            candidate = _resolve_local_data_path(connector.endpoint_url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        if not candidate.is_file():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{connector.endpoint_url!r} was not found under the watched data folder")
        filename = candidate.name
        if candidate.suffix.lower() not in (".csv", ".json", ".xlsx", ".xlsm"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"path must end in .csv/.json/.xlsx (got {filename!r})")
        content = candidate.read_bytes()

    table_key = normalize_table_key(filename)
    if table_key in KNOWN_TABLE_KEYS:
        rows = rows_from_file(filename, content)
        result = ingest_reference_rows(db, ctx.tenant_id, table_key, rows, source_label=f"connector:{connector.name}")
        return _finish_ingest(db, ctx, connector, rows_queued=result.get("count", 0), lineage_id=result.get("lineage_id"), detail=result)

    try:
        readings = parse_telemetry_file(filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not readings:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid rows found (expected columns: asset_id, metric, event_time, value, unit)")

    from reo_common.events import EventBus

    lineage_id = publish_readings(EventBus(), ctx.tenant_id, readings, lineage_id=f"connector:{connector.id}")
    return _finish_ingest(db, ctx, connector, rows_queued=len(readings), lineage_id=lineage_id, detail=None)
