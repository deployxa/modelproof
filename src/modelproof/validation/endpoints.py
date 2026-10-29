"""Loopback-only endpoint validation (spec §10.1).

The evaluation engine must never transmit prompts, outputs, or model files
off the machine. This module is the single authority on what counts as an
acceptable inference target:

  accepted:  http://127.0.0.1[:port]  · http://[::1][:port]  · http://localhost[:port]
             (https also permitted *only* when the host is a verified loopback)
  rejected:  remote hostnames, remote/public IPs, non-HTTP(S) schemes,
             hostnames that resolve to any non-loopback address (DNS bypass),
             credentials-in-URL tricks, and redirects escaping the local target.

`localhost` is accepted only after resolving it and verifying that *every*
resolved address is loopback — we do not silently trust arbitrary DNS.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from ..errors import EndpointPolicyError

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_REDIRECTS = 3


def _is_loopback_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback


def _resolve_host(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise EndpointPolicyError(f"cannot resolve inference host {hostname!r}: {e}") from e
    return sorted({str(info[4][0]) for info in infos})


def validate_endpoint(url: str, *, resolve_dns: bool = True) -> dict:
    """Validate and normalize an inference endpoint. Returns parsed components.

    Raises EndpointPolicyError for anything that is not a strictly local target.
    """
    if not isinstance(url, str) or not url.strip():
        raise EndpointPolicyError("inference endpoint must be a non-empty URL")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise EndpointPolicyError(
            f"unsupported URL scheme {parts.scheme!r} for inference endpoint (expected http/https to loopback)"
        )
    if parts.username or parts.password:
        raise EndpointPolicyError("credentials in the inference endpoint URL are not allowed")
    if not parts.hostname:
        raise EndpointPolicyError("inference endpoint URL has no host")
    host = parts.hostname
    if host.endswith("."):
        host = host[:-1]

    literal_loopback = _is_loopback_ip(host)
    resolved: list[str] = []
    if literal_loopback:
        resolved = [host]
    else:
        if host != "localhost":
            # A non-literal name other than 'localhost' is exactly the DNS-bypass
            # shape we refuse: we do not trust arbitrary names to stay local.
            raise EndpointPolicyError(
                f"refusing non-local inference host {host!r}; only loopback literals or 'localhost' are accepted"
            )
        if not resolve_dns:
            raise EndpointPolicyError(
                "hostname resolution disabled and host is not a loopback literal"
            )
        resolved = _resolve_host(host)
        if not resolved or not all(_is_loopback_ip(ip) for ip in resolved):
            raise EndpointPolicyError(
                f"host {host!r} does not resolve exclusively to loopback addresses (got {resolved}); DNS bypass refused"
            )

    port = parts.port if parts.port is not None else _DEFAULT_PORTS[parts.scheme.lower()]
    if not (1 <= port <= 65535):
        raise EndpointPolicyError(f"invalid port {port}")
    if parts.query or parts.fragment:
        raise EndpointPolicyError("inference endpoint URL must not contain query or fragment")

    return {
        "url": f"{parts.scheme.lower()}://{host}:{port}",
        "scheme": parts.scheme.lower(),
        "host": host,
        "port": port,
        "resolved_ips": resolved,
    }


def validate_redirect(base: dict, location: str) -> dict:
    """Validate a redirect target against the same local-only policy."""
    if not location:
        raise EndpointPolicyError("redirect without Location header")
    parts = urlsplit(location)
    if parts.scheme or parts.netloc:
        return validate_endpoint(location)
    # Relative redirect stays on the same host: scheme/host unchanged, path differs.
    return {**base, "url": f"{base['scheme']}://{base['host']}:{base['port']}{location}"}
