"""Connector Studio (FR-CON-001/002/003): authorised humans register a
client-system API endpoint, an auth method, and payload mapping; secrets go
straight to the vault and are never returned in cleartext; activation
requires a *different* human than the one who created it (maker-checker);
a sandbox dry-run is available before activation; disabling is immediate
and requires no counter-approval.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from reo_common.audit import append_audit_event
from reo_common.models import Connector, CredentialRef
from reo_common.secrets import get_secrets_provider
from reo_common.security import AuthContext
from reo_common.ssrf import check_outbound_url
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, require_permission

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectorCreateRequest(BaseModel):
    name: str
    kind: Literal["generic", "market_data", "database", "scada_bridge"] = "generic"
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
        auth_type=auth_type, credential_masked=masked, created_by=c.created_by, activated_by=c.activated_by,
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
