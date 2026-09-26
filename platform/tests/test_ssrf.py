"""TR-SSRF-01: Connector Studio must reject outbound targets pointing at
loopback/link-local/cloud-metadata/private ranges by default."""

from guardrails.ssrf import check_outbound_host, check_outbound_url


def test_blocks_cloud_metadata_endpoint():
    result = check_outbound_url("http://169.254.169.254/latest/meta-data/")
    assert not result.allowed


def test_blocks_loopback():
    result = check_outbound_url("http://127.0.0.1:8000/admin")
    assert not result.allowed


def test_blocks_disallowed_scheme():
    result = check_outbound_url("file:///etc/passwd")
    assert not result.allowed


def test_allows_public_https_host():
    result = check_outbound_url("https://example.com/webhook")
    assert result.allowed


def test_explicit_allowlist_permits_private_target():
    result = check_outbound_url("http://internal.tenant.local/api", tenant_egress_allowlist=["internal.tenant.local"])
    assert result.allowed


# check_outbound_host() is the scheme-agnostic core check_outbound_url() now
# delegates to (used directly by a `database`-kind connector's raw
# postgresql:// connection string, which has no http(s) scheme for
# check_outbound_url itself to validate) — same IP-blocking behaviour, no
# URL involved.


def test_check_outbound_host_blocks_loopback_directly():
    result = check_outbound_host("127.0.0.1", 5432)
    assert not result.allowed


def test_check_outbound_host_blocks_docker_private_range_by_default():
    # source-db (infrastructure/docker-compose.yml) resolves inside docker's
    # private bridge network — blocked unless explicitly allowlisted, same
    # as any other private IP a human-typed connector target might resolve to.
    result = check_outbound_host("172.17.0.5", 5432)
    assert not result.allowed


def test_check_outbound_host_allowlist_permits_named_internal_service():
    result = check_outbound_host("source-db", 5432, tenant_egress_allowlist=["source-db"])
    assert result.allowed


def test_check_outbound_host_allows_public_host():
    result = check_outbound_host("example.com", 443)
    assert result.allowed
