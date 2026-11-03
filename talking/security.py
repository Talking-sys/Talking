"""Minimal security helpers for the skeleton: HMAC tokens, rate limit, headers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def load_server_secret(data_dir: str | Path, env_key: str = "") -> bytes:
    if env_key and len(env_key) >= 32:
        return env_key.encode()
    env = os.environ.get("APP_SECRET_KEY")
    if env and len(env) >= 32:
        return env.encode()
    p = Path(data_dir) / "secret.key"
    if p.exists():
        return p.read_bytes()
    key = secrets.token_bytes(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(secret: bytes, payload: dict, purpose: str, ttl: int = 3600) -> str:
    body = dict(payload)
    body["_p"] = purpose
    body["_exp"] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(secret, raw, hashlib.sha256).digest()
    return _b64(raw) + "." + _b64(mac)


def unsign(secret: bytes, token: str, purpose: str) -> dict | None:
    try:
        raw_s, mac_s = token.split(".", 1)
        raw, mac = _unb64(raw_s), _unb64(mac_s)
    except (ValueError, Exception):
        return None
    expect = hmac.new(secret, raw, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if body.get("_p") != purpose:
        return None
    if int(body.get("_exp", 0)) < time.time():
        return None
    body.pop("_p", None)
    body.pop("_exp", None)
    return body


class TokenBucket:
    def __init__(self, per_minute: float, burst: int | None = None):
        self.rate = per_minute / 60.0
        self.burst = burst if burst is not None else max(1, int(per_minute))
        self._tokens: dict[str, float] = defaultdict(lambda: float(self.burst))
        self._ts: dict[str, float] = defaultdict(time.time)

    def allow(self, key: str) -> bool:
        now = time.time()
        # Use .get(..., now) so the first hit is not charged a negative dt
        # (defaultdict(time.time) was evaluated *after* `now`, which made
        # burst=1 fail the opening request).
        last = self._ts.get(key, now)
        tokens = min(self.burst, self._tokens.get(key, float(self.burst)) + max(0.0, now - last) * self.rate)
        self._ts[key] = now
        if tokens >= 1:
            self._tokens[key] = tokens - 1
            return True
        self._tokens[key] = tokens
        return False

    def remaining(self, key: str) -> float:
        now = time.time()
        last = self._ts[key] if key in self._ts else now
        tokens = self._tokens[key] if key in self._tokens else float(self.burst)
        return min(self.burst, tokens + max(0.0, now - last) * self.rate)


def client_ip(request) -> str:
    return request.client.host if request.client else "0.0.0.0"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, production: bool = False):
        super().__init__(app)
        self.production = production

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        # frame-src allows the sandboxed mini-browser (http/https only; JS/data
        # schemes are rejected in talking/static/link-preview.js before load).
        # The app chrome must not be nested (frame-ancestors none). Fixture
        # pages used as mini-browser targets allow same-origin framing only.
        embeddable = request.url.path.startswith("/static/fixtures/")
        # Sandboxed iframes get a unique origin, so frame-ancestors 'self' on the
        # fixture would block our own mini-browser. Omit it here; XFO SAMEORIGIN
        # still stops other sites from framing the file.
        ancestors = "" if embeddable else " frame-ancestors 'none';"
        csp = "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; " \
              "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws: wss:; " \
              f"frame-src 'self' https: http:;{ancestors} " \
              "base-uri 'self'; form-action 'self'"
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if embeddable:
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        else:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(self), geolocation=()")
        if self.production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
        return response
