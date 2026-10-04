"""One-shot init steps must wait for their dependency instead of failing the
whole `docker compose up`: SeaweedFS reports healthy before its S3 gateway
accepts connections, and a fresh Postgres restarts once after its healthcheck
first passes."""

import importlib
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "infrastructure" / "seaweedfs-init"))
sys.path.insert(0, str(ROOT / "database"))

bootstrap_buckets = importlib.import_module("bootstrap_buckets")
migrate = importlib.import_module("migrate")


class _Client:
    def __init__(self, failures):
        self.failures, self.calls = list(failures), 0

    def create_bucket(self, Bucket):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)


def _not_ready():
    return EndpointConnectionError(endpoint_url="http://seaweedfs:8333")


def test_bucket_creation_waits_for_the_s3_gateway(monkeypatch):
    monkeypatch.setattr(bootstrap_buckets.time, "sleep", lambda _s: None)
    client = _Client([_not_ready(), _not_ready()])
    bootstrap_buckets._create_with_retry(client, "reo-exports")
    assert client.calls == 3


def test_an_existing_bucket_is_not_an_error(monkeypatch):
    monkeypatch.setattr(bootstrap_buckets.time, "sleep", lambda _s: None)
    exists = ClientError({"Error": {"Code": "BucketAlreadyOwnedByYou"}}, "CreateBucket")
    client = _Client([exists])
    bootstrap_buckets._create_with_retry(client, "reo-exports")
    assert client.calls == 1


def test_a_real_error_still_fails_after_the_attempts_are_used_up(monkeypatch):
    monkeypatch.setattr(bootstrap_buckets.time, "sleep", lambda _s: None)
    with pytest.raises(EndpointConnectionError):
        bootstrap_buckets._create_with_retry(_Client([_not_ready()] * 5), "reo-exports", attempts=3)
    denied = ClientError({"Error": {"Code": "AccessDenied"}}, "CreateBucket")
    with pytest.raises(ClientError):
        bootstrap_buckets._create_with_retry(_Client([denied] * 5), "reo-exports", attempts=3)


def test_migrate_waits_for_the_database(monkeypatch):
    import sqlalchemy

    attempts = {"n": 0}

    class _Conn:
        def __enter__(self):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise sqlalchemy.exc.OperationalError("connect", {}, Exception("connection refused"))
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a, **_k):
            return None

    class _Engine:
        def connect(self):
            return _Conn()

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(migrate.time, "sleep", lambda _s: None)
    migrate.wait_for_database(timeout_seconds=60)
    assert attempts["n"] == 3
