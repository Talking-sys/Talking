"""Sign in with Apple: authorize URL, client-secret JWT, token exchange, JWKS.

Feature is off unless client id, team id, key id, private key, and an exact
https redirect URI are all configured. Never log codes, tokens, keys, or email.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .jwt_es256 import JwtError, encode_es256, load_ec_private_key, verify_es256

log = logging.getLogger("talking.auth")

APPLE_ISS = "https://appleid.apple.com"
APPLE_AUTHORIZE_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
PRIVATE_RELAY_DOMAIN = "privaterelay.appleid.com"
CLIENT_SECRET_TTL_S = 86400
JWKS_TTL_S = 6 * 3600
OAUTH_TTL_S = 10 * 60
NICK_RE = re.compile(r"[^\w.+-]+", re.UNICODE)

REQUIRED_ATTRS = (
    "apple_client_id",
    "apple_team_id",
    "apple_key_id",
    "apple_redirect_uri",
)


class AppleError(Exception):
    def __init__(self, code: str = "apple_error"):
        super().__init__(code)
        self.code = code


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)  # 43 chars; fits login_challenges.provider_ref
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def is_private_relay(email: str | None) -> bool:
    if not email:
        return False
    host = email.rsplit("@", 1)[-1].lower().strip()
    return host == PRIVATE_RELAY_DOMAIN


def nonce_matches(expected: str, got: str | None) -> bool:
    if not expected or not got:
        return False
    if hmac.compare_digest(expected, got):
        return True
    hashed = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    return hmac.compare_digest(hashed, got)


def nickname_from_apple_name(name: dict | None) -> str | None:
    if not isinstance(name, dict):
        return None
    first = str(name.get("firstName") or "").strip()
    if not first:
        return None
    cleaned = NICK_RE.sub("", first)
    cleaned = cleaned[:32].strip("._-")
    return cleaned or None


def parse_apple_user_form(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _pem_from_settings(settings) -> str:
    raw = (getattr(settings, "apple_private_key", "") or "").strip()
    if raw:
        return raw.replace("\\n", "\n")
    path = (getattr(settings, "apple_private_key_file", "") or "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def apple_config_flags(settings) -> list[bool]:
    pem = _pem_from_settings(settings)
    return [
        bool((getattr(settings, name, "") or "").strip())
        for name in REQUIRED_ATTRS
    ] + [bool(pem.strip())]


def apple_configured(settings) -> bool:
    return all(apple_config_flags(settings))


def apple_partial(settings) -> bool:
    flags = apple_config_flags(settings)
    return any(flags) and not all(flags)


def validate_apple_redirect_uri(uri: str) -> str:
    value = (uri or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise RuntimeError(
            "APPLE_REDIRECT_URI must be an exact https:// URL with no fragment"
        )
    return value


def validate_apple_settings(settings) -> None:
    if apple_partial(settings):
        raise RuntimeError(
            "Sign in with Apple is partially configured; set APPLE_CLIENT_ID, "
            "APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY or APPLE_PRIVATE_KEY_FILE, "
            "and APPLE_REDIRECT_URI, or leave all empty to keep the feature off"
        )
    if not apple_configured(settings):
        return
    validate_apple_redirect_uri(settings.apple_redirect_uri)
    try:
        load_ec_private_key(_pem_from_settings(settings))
    except JwtError as exc:
        raise RuntimeError("APPLE_PRIVATE_KEY is not a valid P-256 PKCS#8 PEM") from exc


@dataclass
class AppleIdentity:
    subject: str
    email: str | None
    email_verified: bool
    private_relay: bool
    claims: dict[str, Any]


class AppleClient:
    def __init__(self, settings, *, transport: httpx.BaseTransport | None = None):
        self.client_id = (settings.apple_client_id or "").strip()
        self.team_id = (settings.apple_team_id or "").strip()
        self.key_id = (settings.apple_key_id or "").strip()
        self.redirect_uri = validate_apple_redirect_uri(settings.apple_redirect_uri)
        self.authorize_url = (getattr(settings, "apple_authorize_url", "") or "").strip() or APPLE_AUTHORIZE_URL
        self.token_url = (getattr(settings, "apple_token_url", "") or "").strip() or APPLE_TOKEN_URL
        self.jwks_url = (getattr(settings, "apple_jwks_url", "") or "").strip() or APPLE_JWKS_URL
        self._pem = _pem_from_settings(settings)
        self._key = load_ec_private_key(self._pem)
        self.transport = transport
        self._jwks: dict | None = None
        self._jwks_at = 0.0

    def authorization_url(self, *, state: str, nonce: str, challenge: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def client_secret_jwt(self, *, now: int | None = None) -> str:
        ts = int(now if now is not None else time.time())
        payload = {
            "iss": self.team_id,
            "iat": ts,
            "exp": ts + CLIENT_SECRET_TTL_S,
            "aud": APPLE_ISS,
            "sub": self.client_id,
        }
        return encode_es256(payload, self._key, kid=self.key_id)

    def _http(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": 15.0}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def fetch_jwks(self, *, force: bool = False) -> dict:
        if self._jwks is not None and not force and (time.time() - self._jwks_at) < JWKS_TTL_S:
            return self._jwks
        async with self._http() as client:
            response = await client.get(self.jwks_url)
        if response.status_code != 200:
            raise AppleError("apple_jwks")
        data = response.json()
        if not isinstance(data, dict) or not data.get("keys"):
            raise AppleError("apple_jwks")
        self._jwks = data
        self._jwks_at = time.time()
        return data

    async def exchange_code(self, code: str, verifier: str) -> dict:
        secret = self.client_secret_jwt()
        data = {
            "client_id": self.client_id,
            "client_secret": secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }
        async with self._http() as client:
            response = await client.post(self.token_url, data=data)
        if response.status_code != 200:
            log.info("apple token exchange failed status=%s", response.status_code)
            raise AppleError("apple_token")
        body = response.json()
        if not isinstance(body, dict) or not body.get("id_token"):
            raise AppleError("apple_token")
        return body

    async def validate_id_token(self, token: str, *, nonce: str) -> AppleIdentity:
        keys = await self.fetch_jwks()
        try:
            claims = verify_es256(token, keys, audience=self.client_id, issuer=APPLE_ISS)
        except JwtError:
            self._jwks = None
            keys = await self.fetch_jwks(force=True)
            try:
                claims = verify_es256(token, keys, audience=self.client_id, issuer=APPLE_ISS)
            except JwtError as exc:
                raise AppleError("invalid_id_token") from exc
        got_nonce = claims.get("nonce")
        if got_nonce is not None and not nonce_matches(nonce, str(got_nonce)):
            raise AppleError("invalid_nonce")
        if claims.get("nonce_supported") is True and not got_nonce:
            raise AppleError("invalid_nonce")
        if not got_nonce:
            # nonce was sent; Apple must echo it when the platform supports it.
            raise AppleError("invalid_nonce")
        sub = str(claims.get("sub") or "").strip()
        if not sub:
            raise AppleError("invalid_id_token")
        email = str(claims.get("email") or "").strip() or None
        verified_raw = claims.get("email_verified")
        verified = str(verified_raw).lower() in ("true", "1") if verified_raw is not None else False
        private = False
        if claims.get("is_private_email") is not None:
            private = str(claims.get("is_private_email")).lower() in ("true", "1")
        elif is_private_relay(email):
            private = True
        return AppleIdentity(
            subject=sub, email=email, email_verified=verified,
            private_relay=private, claims=claims,
        )

    async def validate_notification(self, token: str) -> dict[str, Any]:
        keys = await self.fetch_jwks()
        try:
            return verify_es256(token, keys, audience=self.client_id, issuer=APPLE_ISS)
        except JwtError:
            self._jwks = None
            keys = await self.fetch_jwks(force=True)
            try:
                return verify_es256(token, keys, audience=self.client_id, issuer=APPLE_ISS)
            except JwtError as exc:
                raise AppleError("invalid_notification") from exc


def build_apple_client(settings) -> AppleClient | None:
    if not apple_configured(settings):
        return None
    return AppleClient(settings)
