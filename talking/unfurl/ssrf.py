"""SSRF guards for the link-preview unfurl service.

Block private / loopback / link-local / metadata / reserved addresses
*after DNS resolution*, including every redirect hop. Only http/https on
ports 80 and 443 are allowed. DNS rebinding is mitigated by pinning the
resolved IP for the TCP connection (see fetch.py).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

# Ports the unfurl client may connect to.
ALLOWED_PORTS = {80, 443}
ALLOWED_SCHEMES = {"http", "https"}

# Hostnames that must never be fetched even if they somehow resolved public.
BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "internal",
}

# Extra networks not always covered by ipaddress.is_private (e.g. CGNAT on 3.12.3).
BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),       # CGNAT / shared address space
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),      # link-local + cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),        # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),       # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),         # multicast
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),       # IPv4-mapped IPv6
    ipaddress.ip_network("64:ff9b::/96"),        # NAT64
    ipaddress.ip_network("fc00::/7"),            # unique local
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("fd00:ec2::/32"),       # AWS IMDS v6-ish
)

MAX_URL_LEN = 2048


class SSRFError(ValueError):
    """Raised when a URL or resolved address is not safe to fetch."""


def _host_suffix_blocked(host: str) -> bool:
    h = host.rstrip(".").lower()
    if h in BLOCKED_HOSTS:
        return True
    for suffix in (".localhost", ".local", ".internal", ".intranet", ".corp",
                   ".home", ".lan", ".invalid"):
        if h.endswith(suffix):
            return True
    return False


def canonicalize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """Unwrap IPv4-mapped IPv6 so ::ffff:127.0.0.1 is treated as 127.0.0.1."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        return ip.sixtofour
    if isinstance(ip, ipaddress.IPv6Address) and ip.teredo is not None:
        # teredo = (server, client); the client is the embedded IPv4
        return ip.teredo[1]
    return ip


def is_blocked_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        addr = ip if isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ipaddress.ip_address(ip)
    except ValueError:
        return True
    addr = canonicalize_ip(addr)
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or not addr.is_global
    ):
        return True
    for net in BLOCKED_NETWORKS:
        try:
            if addr in net:
                return True
        except TypeError:
            continue
    return False


def assert_public_ip(ip: str) -> None:
    if is_blocked_ip(ip):
        raise SSRFError("blocked")


def parse_literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return an IP if `host` is a literal (including decimal / hex IPv4)."""
    h = host.strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if h.isdigit():
        try:
            return ipaddress.IPv4Address(int(h))
        except (ValueError, OverflowError):
            raise SSRFError("blocked") from None
    if re.fullmatch(r"0x[0-9a-fA-F]+", h):
        try:
            return ipaddress.IPv4Address(int(h, 16))
        except (ValueError, OverflowError):
            raise SSRFError("blocked") from None
    # Dotted quads with octal/leading zeros are rejected (glibc inet_aton quirks).
    if re.fullmatch(r"[0-9.]+", h) and h.count(".") == 3:
        try:
            return ipaddress.IPv4Address(h)
        except ValueError as e:
            raise SSRFError("blocked") from e
    return None


def ascii_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    if host.startswith("[") and host.endswith("]"):
        return host
    if ":" in host:  # IPv6 literal — do not IDNA-encode
        return host
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise SSRFError("blocked") from e


def normalize_url(url: str) -> str:
    """Parse, validate scheme/port/host, return a normalized URL string."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        raise SSRFError("blocked")
    raw = url.strip()
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFError("blocked")
    if parsed.username is not None or parsed.password is not None:
        raise SSRFError("blocked")
    host = parsed.hostname
    if not host:
        raise SSRFError("blocked")
    host = ascii_host(host)
    if _host_suffix_blocked(host):
        raise SSRFError("blocked")
    port = parsed.port
    default_port = 443 if scheme == "https" else 80
    if port is None:
        port = default_port
    if port not in ALLOWED_PORTS:
        raise SSRFError("blocked")
    default = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    literal = parse_literal_ip(host)
    if literal is not None and isinstance(literal, ipaddress.IPv6Address):
        netloc = f"[{host}]" if default else f"[{host}]:{port}"
    else:
        netloc = host if default else f"{host}:{port}"
    path = parsed.path or "/"
    query = parsed.query
    # Fragments never hit the wire.
    normalized = urlunparse((scheme, netloc, path, "", query, ""))
    # Literal IP in the host? Check immediately (no DNS needed).
    literal = parse_literal_ip(host)
    if literal is not None:
        assert_public_ip(str(literal))
    return normalized


def resolve_and_pin(
    host: str,
    port: int,
    *,
    getaddrinfo=socket.getaddrinfo,
) -> str:
    """Resolve host, reject if any address is blocked, return one public IP to pin."""
    host = ascii_host(host)
    if _host_suffix_blocked(host):
        raise SSRFError("blocked")
    literal = parse_literal_ip(host)
    if literal is not None:
        assert_public_ip(str(literal))
        return str(literal)
    try:
        infos = getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError) as e:
        raise SSRFError("blocked") from e
    if not infos:
        raise SSRFError("blocked")
    public: list[str] = []
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0]
        if is_blocked_ip(ip):
            # Any blocked address → reject the name (happy-eyeballs / rebinding).
            raise SSRFError("blocked")
        public.append(ip)
    if not public:
        raise SSRFError("blocked")
    return public[0]


def split_url(url: str) -> tuple[str, str, int, str]:
    """Return (scheme, host, port, path_and_query) from a normalized URL."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query
    return scheme, host, port, path
