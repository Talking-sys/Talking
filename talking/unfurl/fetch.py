"""SSRF-safe HTTP GET: IP pin after DNS, no cookies, size/type/redirect limits."""
from __future__ import annotations

import gzip
import http.client
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin

from .ssrf import SSRFError, normalize_url, resolve_and_pin, split_url


class FetchError(Exception):
    """Valid public target, but the response is unusable (type/size/timeout)."""


USER_AGENT = "TalkingUnfurl/0.1 (+https://github.com/Talking-sys/Talking; hateaboutyou111@icloud.com)"
HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,application/json;q=0.5,*/*;q=0.1"
IMAGE_ACCEPT = "image/jpeg,image/png,image/webp,image/gif,image/*;q=0.8"
DEFAULT_TIMEOUT = 8.0
CONNECT_TIMEOUT = 3.0
MAX_REDIRECTS = 4
MAX_HTML_BYTES = 1_048_576
MAX_IMAGE_BYTES = 5_242_880

HopFn = Callable[..., "RawResponse"]


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    elapsed_s: float = 0.0


@dataclass
class SafeFetcher:
    """Outbound GET used only by unfurl. Cookies are never stored or sent."""

    timeout: float = DEFAULT_TIMEOUT
    connect_timeout: float = CONNECT_TIMEOUT
    max_redirects: int = MAX_REDIRECTS
    max_bytes: int = MAX_HTML_BYTES
    getaddrinfo: Callable = socket.getaddrinfo
    hop: HopFn | None = None
    allowed_content_types: tuple[str, ...] = ("text/html", "application/xhtml+xml", "application/xml")
    accept: str = HTML_ACCEPT

    def get(self, url: str, *, max_bytes: int | None = None,
            allowed_content_types: tuple[str, ...] | None = None,
            accept: str | None = None) -> RawResponse:
        hop = self.hop or self._real_hop
        current = normalize_url(url)
        seen: set[str] = set()
        limit = self.max_redirects
        mb = max_bytes if max_bytes is not None else self.max_bytes
        types = allowed_content_types if allowed_content_types is not None else self.allowed_content_types
        acc = accept if accept is not None else self.accept
        last: RawResponse | None = None
        for _ in range(limit + 1):
            if current in seen:
                raise FetchError("type")
            seen.add(current)
            scheme, host, port, path = split_url(current)
            ip = resolve_and_pin(host, port, getaddrinfo=self.getaddrinfo)
            last = hop(
                url=current, scheme=scheme, host=host, port=port, path=path, ip=ip,
                timeout=self.timeout, connect_timeout=self.connect_timeout,
                max_bytes=mb, accept=acc,
            )
            last.headers.pop("set-cookie", None)
            last.headers.pop("set-cookie2", None)
            if last.status in (301, 302, 303, 307, 308):
                loc = last.headers.get("location")
                if not loc:
                    raise FetchError("type")
                nxt = urljoin(current, loc.strip())
                current = normalize_url(nxt)
                continue
            self._check_content_type(last.headers.get("content-type", ""), types)
            if len(last.body) > mb:
                raise FetchError("oversized")
            return last
        raise FetchError("redirects")

    def get_image(self, url: str) -> RawResponse:
        return self.get(
            url,
            max_bytes=MAX_IMAGE_BYTES,
            allowed_content_types=("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"),
            accept=IMAGE_ACCEPT,
        )

    @staticmethod
    def _check_content_type(header: str, allowed: tuple[str, ...]) -> None:
        ct = (header or "").split(";", 1)[0].strip().lower()
        if not ct:
            # Some servers omit type; allow and let the parser / Pillow fail closed.
            return
        if ct in ("application/octet-stream", "binary/octet-stream"):
            return
        if any(ct == a or ct.startswith(a + "+") for a in allowed):
            return
        # Prefix match for e.g. image/jpeg
        if any(ct.startswith(a) for a in allowed if a.endswith("/")):
            return
        raise FetchError("type")

    def _real_hop(self, *, url: str, scheme: str, host: str, port: int, path: str,
                  ip: str, timeout: float, connect_timeout: float, max_bytes: int,
                  accept: str) -> RawResponse:
        t0 = time.monotonic()
        ctx = ssl.create_default_context() if scheme == "https" else None
        if scheme == "https":
            conn: http.client.HTTPConnection = _PinnedHTTPS(host, ip, port, timeout, ctx, connect_timeout)
        else:
            conn = _PinnedHTTP(host, ip, port, timeout, connect_timeout)
        try:
            conn.request(
                "GET",
                path or "/",
                headers={
                    "Host": host if ":" not in host else f"[{host}]",
                    "User-Agent": USER_AGENT,
                    "Accept": accept,
                    "Accept-Encoding": "identity",
                    "Accept-Language": "en,ru;q=0.8",
                    "Connection": "close",
                },
            )
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            # Never persist cookies; also drop them from what we keep.
            headers.pop("set-cookie", None)
            headers.pop("set-cookie2", None)
            body = _read_limited(resp, max_bytes)
            enc = (headers.get("content-encoding") or "").lower()
            if enc == "gzip":
                try:
                    body = gzip.decompress(body)
                except OSError as e:
                    raise FetchError("gzip") from e
            if len(body) > max_bytes:
                raise FetchError("oversized")
            return RawResponse(status=resp.status, headers=headers, body=body, url=url,
                               elapsed_s=time.monotonic() - t0)
        except (SSRFError, FetchError):
            raise
        except (OSError, http.client.HTTPException, TimeoutError, ssl.SSLError) as e:
            raise FetchError("network") from e
        finally:
            try:
                conn.close()
            except Exception:
                pass


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float, connect_timeout: float):
        super().__init__(host, port=port, timeout=timeout, blocksize=16 * 1024)
        self._ip = ip
        self._connect_timeout = connect_timeout

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self._connect_timeout)
        self.sock.settimeout(self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float,
                 context: ssl.SSLContext, connect_timeout: float):
        super().__init__(host, port=port, timeout=timeout, context=context, blocksize=16 * 1024)
        self._ip = ip
        self._connect_timeout = connect_timeout

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self._connect_timeout)
        sock.settimeout(self.timeout)
        server_hostname = self.host
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


def _read_limited(resp: http.client.HTTPResponse, max_bytes: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = resp.read(min(65536, max_bytes + 1 - len(buf)))
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise FetchError("oversized")
    return bytes(buf)
