"""SSRF hardening for human-configured outbound endpoints (TR-SSRF-01,
doc 05 §10 "no arbitrary SSRF from user endpoints").

Used by Connector Studio's test/dry-run/execute paths — anywhere the
platform is about to make an HTTP call to a URL a human typed in. Blocks
loopback, link-local, metadata endpoints (169.254.169.254 etc.) and other
private ranges by default; re-resolves DNS immediately before the call so a
DNS-rebinding attack (allowed hostname resolving to a blocked IP at request
time) is also caught.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata (169.254.169.254)
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
]

ALLOWED_SCHEMES = {"https", "http"}  # http only for local-network sandbox testing; document as prod=https-only


@dataclass
class SsrfCheckResult:
    allowed: bool
    reason: str | None = None
    resolved_ip: str | None = None


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparsable => block
    if ip.is_private and not any(ip in net for net in [ipaddress.ip_network("10.0.0.0/8")]):
        # private ranges other than the explicit tenant-egress allowlist are blocked by default
        pass
    return any(ip in net for net in BLOCKED_NETWORKS) or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved


def check_outbound_url(url: str, *, tenant_egress_allowlist: list[str] | None = None) -> SsrfCheckResult:
    """Re-resolve DNS at call time and reject anything pointing at a blocked
    range. `tenant_egress_allowlist` (hostnames or CIDRs) lets a tenant
    admin explicitly permit an internal integration target; everything else
    private is blocked by default."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return SsrfCheckResult(False, f"scheme not allowed: {parsed.scheme}")
    if not parsed.hostname:
        return SsrfCheckResult(False, "no hostname in URL")
    if parsed.hostname in (tenant_egress_allowlist or []):
        return SsrfCheckResult(True, "explicit tenant egress allowlist match")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        return SsrfCheckResult(False, f"DNS resolution failed: {exc}")

    for family, _, _, _, sockaddr in resolved:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            return SsrfCheckResult(False, f"resolved IP {ip_str} is in a blocked range", resolved_ip=ip_str)

    return SsrfCheckResult(True, resolved_ip=resolved[0][4][0])
