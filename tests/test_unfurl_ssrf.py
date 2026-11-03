"""SSRF: private IPs, DNS rebinding-style, redirects to internal, file://, oversized."""
import socket

import pytest

from talking.unfurl.fetch import FetchError, RawResponse, SafeFetcher
from talking.unfurl.ssrf import SSRFError, is_blocked_ip, normalize_url, resolve_and_pin

PUBLIC_IP = "93.184.216.34"


def public_gai(host, port, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 80))]


def gai_to(*ips):
    def _gai(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80)) for ip in ips]
    return _gai


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "https://127.0.0.1/",
    "http://localhost/",
    "http://10.0.0.5/secret",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/",
    "http://[::1]/",
    "http://[fd12:3456:789a::1]/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,hi",
    "ftp://example.com/",
    "http://example.com:22/",
    "http://example.com:8080/",
    "http://user:pass@example.com/",
    "http://2130706433/",  # decimal 127.0.0.1
])
def test_normalize_blocks_dangerous_urls(url):
    with pytest.raises(SSRFError):
        normalize_url(url)


def test_normalize_allows_https_public_host():
    assert normalize_url("https://example.com/a?b=1#frag").startswith("https://example.com/a")


def test_blocked_ip_helpers():
    assert is_blocked_ip("127.0.0.1")
    assert is_blocked_ip("10.1.2.3")
    assert is_blocked_ip("169.254.169.254")
    assert is_blocked_ip("::1")
    assert is_blocked_ip("::ffff:127.0.0.1")
    assert is_blocked_ip("100.64.1.1")
    assert not is_blocked_ip("8.8.8.8")
    assert not is_blocked_ip(PUBLIC_IP)


def test_dns_rebinding_style_all_private():
    with pytest.raises(SSRFError):
        resolve_and_pin("evil.example", 443, getaddrinfo=gai_to("127.0.0.1"))


def test_dns_rebinding_style_mixed_public_private():
    # Any blocked address on the name → reject (happy-eyeballs / rebind).
    with pytest.raises(SSRFError):
        resolve_and_pin("evil.example", 443, getaddrinfo=gai_to(PUBLIC_IP, "127.0.0.1"))


def test_pin_uses_public_when_all_public():
    ip = resolve_and_pin("example.com", 443, getaddrinfo=gai_to(PUBLIC_IP))
    assert ip == PUBLIC_IP


def test_redirect_to_internal_blocked():
    calls = []

    def hop(**kw):
        calls.append(kw["url"])
        if len(calls) == 1:
            return RawResponse(302, {"location": "http://127.0.0.1/admin"}, b"", kw["url"])
        return RawResponse(200, {"content-type": "text/html"}, b"<html>", kw["url"])

    fetcher = SafeFetcher(getaddrinfo=public_gai, hop=hop)
    with pytest.raises(SSRFError):
        fetcher.get("https://example.com/open")
    assert calls == ["https://example.com/open"]


def test_redirect_to_file_blocked():
    def hop(**kw):
        return RawResponse(302, {"location": "file:///etc/passwd"}, b"", kw["url"])

    fetcher = SafeFetcher(getaddrinfo=public_gai, hop=hop)
    with pytest.raises(SSRFError):
        fetcher.get("https://example.com/x")


def test_oversized_body_rejected():
    def hop(**kw):
        return RawResponse(200, {"content-type": "text/html"}, b"x" * 50, kw["url"])

    fetcher = SafeFetcher(getaddrinfo=public_gai, hop=hop, max_bytes=16)
    with pytest.raises(FetchError):
        fetcher.get("https://example.com/big")


def test_no_cookies_sent_even_if_set_cookie():
    def hop(**kw):
        return RawResponse(
            200,
            {"content-type": "text/html", "set-cookie": "sid=abc"},
            b"<html><title>Ok</title></html>",
            kw["url"],
        )

    fetcher = SafeFetcher(getaddrinfo=public_gai, hop=hop)
    r = fetcher.get("https://example.com/")
    assert "set-cookie" not in r.headers
    # Real hop never attaches a Cookie header (no jar; Accept-Encoding identity).
    import inspect
    src = inspect.getsource(SafeFetcher._real_hop)
    assert "Cookie" not in src
    assert '"Host"' in src


def test_metadata_hostname_blocked():
    with pytest.raises(SSRFError):
        normalize_url("http://metadata.google.internal/")
    with pytest.raises(SSRFError):
        resolve_and_pin("metadata.google.internal", 80, getaddrinfo=public_gai)
