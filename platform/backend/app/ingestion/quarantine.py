"""Landing/quarantine zone (doc 05 §8): anything that fails ingestion
validation lands here as a JSON object in object storage instead of being
silently dropped, so a data-quality agent or a human can triage it later.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from reo_common.config import get_settings

log = logging.getLogger("api.ingestion.quarantine")
settings = get_settings()


@lru_cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def quarantine_payload(kind: str, payload: dict, *, reason: str) -> str:
    key = f"{kind}/{datetime.now(timezone.utc):%Y/%m/%d}/{uuid.uuid4().hex}.json"
    body = json.dumps({"reason": reason, "quarantined_at": datetime.now(timezone.utc).isoformat(), "payload": payload})
    try:
        _client().put_object(Bucket=settings.s3_bucket_quarantine, Key=key, Body=body.encode("utf-8"), ContentType="application/json")
    except Exception:
        log.exception("failed to write quarantine object %s (reason=%s) — logging payload instead", key, reason)
        log.warning("quarantined payload: %s", body[:2000])
        return ""
    log.info("quarantined %s payload: %s (reason=%s)", kind, key, reason)
    return key


def quarantine_raw_file(kind: str, content: bytes, *, filename: str, reason: str) -> str:
    """Like quarantine_payload but for raw file bytes rather than a JSON-
    serializable payload — used for an unrecognized upload awaiting a
    human's mapping-proposal review (backend/app/ingestion/
    generic_table_mapper.py), where the original bytes need to be
    re-fetched later to actually ingest it once approved. Unlike
    quarantine_payload, failures here raise rather than degrading to a log
    line: without the stored bytes, approving the proposal later would be
    impossible, so the proposal must not be created if this fails."""
    key = f"{kind}/{datetime.now(timezone.utc):%Y/%m/%d}/{uuid.uuid4().hex}-{filename}"
    _client().put_object(Bucket=settings.s3_bucket_quarantine, Key=key, Body=content, ContentType="application/octet-stream")
    log.info("quarantined %s file: %s (reason=%s)", kind, key, reason)
    return key


def fetch_quarantined_file(key: str) -> bytes:
    return _client().get_object(Bucket=settings.s3_bucket_quarantine, Key=key)["Body"].read()
