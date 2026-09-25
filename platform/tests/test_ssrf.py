"""TR-SSRF-01: Connector Studio must reject outbound targets pointing at
loopback/link-local/cloud-metadata/private ranges by default."""

from guardrails.ssrf import check_outbound_url


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
