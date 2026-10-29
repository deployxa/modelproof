"""Loopback-only endpoint policy (spec §10.1) — the local-only contract."""

from __future__ import annotations

import pytest

from modelproof.errors import EndpointPolicyError
from modelproof.validation.endpoints import validate_endpoint, validate_redirect


class TestAccepted:
    def test_ipv4_loopback(self):
        parsed = validate_endpoint("http://127.0.0.1:8080")
        assert parsed["host"] == "127.0.0.1" and parsed["port"] == 8080

    def test_ipv6_loopback(self):
        parsed = validate_endpoint("http://[::1]:8899")
        assert parsed["host"] == "::1" and parsed["port"] == 8899

    def test_localhost_resolves_to_loopback(self):
        parsed = validate_endpoint("http://localhost:1234")
        assert parsed["resolved_ips"], "localhost must be resolved, not trusted blindly"
        import ipaddress

        for ip in parsed["resolved_ips"]:
            assert ipaddress.ip_address(ip).is_loopback

    def test_default_port(self):
        assert validate_endpoint("http://127.0.0.1")["port"] == 80


class TestRejected:
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/v1",
            "http://api.openai.com/v1",
            "https://huggingface.co",
            "http://8.8.8.8/",
            "http://192.168.1.10:8080",  # private but NOT loopback
            "http://10.0.0.1/",
            "ftp://127.0.0.1/",
            "file:///etc/passwd",
            "gopher://localhost",
            "http://user:pass@127.0.0.1:8080",  # credentials
            "http://127.0.0.1:8080/path?x=1",  # query strings not accepted
            "",
            "not a url",
        ],
    )
    def test_rejected_targets(self, url):
        with pytest.raises(EndpointPolicyError):
            validate_endpoint(url)

    def test_dns_bypass_refused(self, monkeypatch):
        """A hostile resolver mapping 'localhost' to a public IP must fail closed."""
        import socket

        def fake_getaddrinfo(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(EndpointPolicyError, match="does not resolve exclusively to loopback"):
            validate_endpoint("http://localhost:8080")

    def test_unresolvable_host_refused(self, monkeypatch):
        import socket

        def fail(*a, **k):
            raise socket.gaierror("no DNS")

        monkeypatch.setattr(socket, "getaddrinfo", fail)
        with pytest.raises(EndpointPolicyError):
            validate_endpoint("http://localhost:8080")


class TestRedirects:
    BASE = {"url": "http://127.0.0.1:8080", "scheme": "http", "host": "127.0.0.1", "port": 8080}

    def test_relative_redirect_allowed(self):
        out = validate_redirect(self.BASE, "/v1/other")
        assert out["url"] == "http://127.0.0.1:8080/v1/other"

    def test_redirect_to_remote_refused(self):
        with pytest.raises(EndpointPolicyError):
            validate_redirect(self.BASE, "http://example.com/steal")

    def test_redirect_to_remote_ip_refused(self):
        with pytest.raises(EndpointPolicyError):
            validate_redirect(self.BASE, "http://10.1.2.3/steal")

    def test_empty_location_refused(self):
        with pytest.raises(EndpointPolicyError):
            validate_redirect(self.BASE, "")
