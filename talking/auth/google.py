"""Google Sign-In via OpenID Connect (authorization code + PKCE S256).

Scopes are only ``openid email profile``. Tokens stay on the server: the
browser never sees Google access/refresh/ID tokens (no localStorage).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes

log = logging.getLogger("talking.auth")

GOOGLE_SCOPES = ("openid", "email", "profile")
GOOGLE_SCOPE_STRING = "openid email profile"
PLATFORMS = ("web", "android", "ios")
DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
DEFAULT_ISSUER = "https://accounts.google.com"
JWKS_TTL_S = 3600
HTTP_TIMEOUT_S = 10.0
JWT_LEEWAY_S = 60
_PKCE_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


class GoogleOidcError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _b64url_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pkce_verifier() -> str:
    # 32 bytes → 43-char base64url, within RFC 7636's 43–128 window.
    verifier = secrets.token_urlsafe(32)
    if not (43 <= len(verifier) <= 128) or any(c not in _PKCE_UNRESERVED for c in verifier):
        raise RuntimeError("pkce_verifier")
    return verifier


def pkce_s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url_nopad(digest)


def new_oauth_token() -> str:
    return secrets.token_urlsafe(32)


def is_https_redirect_uri(uri: str) -> bool:
    """Exact-match candidates must be HTTPS, no userinfo, no fragment."""
    try:
        parsed = urlparse((uri or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    if not parsed.netloc or parsed.username or parsed.password:
        return False
    if parsed.fragment:
        return False
    return True


def _truthy_env(raw: str) -> bool | None:
    v = (raw or "").strip().lower()
    if not v:
        return None
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def _strip(raw: str | None) -> str:
    return (raw or "").strip()


def unverified_nonce(token: str) -> str:
    """Read nonce from a JWT payload without trusting the signature."""
    try:
        parts = (token or "").split(".")
        if len(parts) != 3:
            return ""
        claims = json.loads(_b64url_decode(parts[1]))
        nonce = claims.get("nonce") if isinstance(claims, dict) else None
        return nonce if isinstance(nonce, str) else ""
    except Exception:
        return ""


def allowed_issuers(configured: str) -> frozenset[str]:
    configured = _strip(configured).rstrip("/") or DEFAULT_ISSUER
    out = {configured}
    if configured.startswith("https://"):
        out.add(configured[len("https://"):])
    elif "://" not in configured:
        out.add("https://" + configured)
    # Google's documented iss values when using the default issuer.
    if configured in (DEFAULT_ISSUER, "accounts.google.com"):
        out.update({DEFAULT_ISSUER, "accounts.google.com"})
    return frozenset(out)


def rsa_public_from_jwk(jwk: dict) -> rsa.RSAPublicKey:
    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


class GoogleOidc:
    def __init__(self, settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._jwks: dict[str, Any] | None = None
        self._jwks_at = 0.0

    def _http(self) -> httpx.AsyncClient:
        kw: dict[str, Any] = {"timeout": HTTP_TIMEOUT_S, "follow_redirects": False}
        if self._transport is not None:
            kw["transport"] = self._transport
        return httpx.AsyncClient(**kw)

    def forced_off(self) -> bool:
        flag = _truthy_env(getattr(self.settings, "google_oauth_enabled", "") or "")
        return flag is False

    def client_id(self, platform: str) -> str:
        platform = (platform or "").strip().lower()
        mapping = {
            "web": _strip(getattr(self.settings, "google_client_id_web", "")),
            "android": _strip(getattr(self.settings, "google_client_id_android", "")),
            "ios": _strip(getattr(self.settings, "google_client_id_ios", "")),
        }
        return mapping.get(platform, "")

    def client_secret(self, platform: str) -> str:
        if (platform or "").strip().lower() != "web":
            return ""
        return _strip(getattr(self.settings, "google_client_secret_web", ""))

    def redirect_uri(self, platform: str) -> str:
        platform = (platform or "").strip().lower()
        per = {
            "web": _strip(getattr(self.settings, "google_redirect_uri_web", "")),
            "android": _strip(getattr(self.settings, "google_redirect_uri_android", "")),
            "ios": _strip(getattr(self.settings, "google_redirect_uri_ios", "")),
        }
        return per.get(platform, "") or _strip(getattr(self.settings, "google_redirect_uri", ""))

    def platforms(self) -> dict[str, bool]:
        return {p: self.platform_ready(p) for p in PLATFORMS}

    def platform_ready(self, platform: str) -> bool:
        if self.forced_off():
            return False
        platform = (platform or "").strip().lower()
        if platform not in PLATFORMS:
            return False
        if not is_https_redirect_uri(self.redirect_uri(platform)):
            return False
        if not self.client_id(platform):
            return False
        if platform == "web" and not self.client_secret(platform):
            return False
        return True

    def enabled(self) -> bool:
        if self.forced_off():
            return False
        return any(self.platform_ready(p) for p in PLATFORMS)

    def duplicate_client_ids(self) -> bool:
        ids = [self.client_id(p) for p in PLATFORMS if self.client_id(p)]
        return len(ids) != len(set(ids))

    @property
    def auth_url(self) -> str:
        return _strip(getattr(self.settings, "google_auth_url", "") or "") or DEFAULT_AUTH_URL

    @property
    def token_url(self) -> str:
        return _strip(getattr(self.settings, "google_token_url", "") or "") or DEFAULT_TOKEN_URL

    @property
    def jwks_url(self) -> str:
        return _strip(getattr(self.settings, "google_jwks_url", "") or "") or DEFAULT_JWKS_URL

    @property
    def issuer(self) -> str:
        return _strip(getattr(self.settings, "google_issuer", "") or "") or DEFAULT_ISSUER

    def authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        nonce: str,
        challenge: str,
    ) -> str:
        # Incremental consent is out of scope: never add Gmail/Drive/etc.
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": GOOGLE_SCOPE_STRING,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.auth_url}?{query}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str = "",
    ) -> dict[str, Any]:
        data = {
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        if client_secret:
            data["client_secret"] = client_secret
        try:
            async with self._http() as client:
                r = await client.post(self.token_url, data=data)
        except Exception:
            log.warning("google token exchange failed")
            raise GoogleOidcError("google_unavailable")
        if r.status_code >= 400:
            log.warning("google token exchange status=%s", r.status_code)
            raise GoogleOidcError("invalid_grant")
        try:
            body = r.json()
        except Exception:
            raise GoogleOidcError("invalid_grant")
        if not isinstance(body, dict) or not body.get("id_token"):
            raise GoogleOidcError("invalid_id_token")
        return body

    async def fetch_jwks(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._jwks is not None and (now - self._jwks_at) < JWKS_TTL_S:
            return self._jwks
        try:
            async with self._http() as client:
                r = await client.get(self.jwks_url)
        except Exception:
            log.warning("google jwks fetch failed")
            if self._jwks is not None:
                return self._jwks
            raise GoogleOidcError("google_unavailable")
        if r.status_code >= 400:
            log.warning("google jwks status=%s", r.status_code)
            if self._jwks is not None:
                return self._jwks
            raise GoogleOidcError("google_unavailable")
        try:
            body = r.json()
        except Exception:
            raise GoogleOidcError("google_unavailable")
        if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
            raise GoogleOidcError("google_unavailable")
        self._jwks = body
        self._jwks_at = now
        return body

    def _key_for_kid(self, jwks: dict[str, Any], kid: str):
        for jwk in jwks.get("keys") or []:
            if not isinstance(jwk, dict):
                continue
            if jwk.get("kid") == kid and jwk.get("kty") == "RSA":
                if jwk.get("alg") and jwk.get("alg") != "RS256":
                    continue
                try:
                    return rsa_public_from_jwk(jwk)
                except Exception:
                    continue
        return None

    async def verify_id_token(self, token: str, *, audience: str, nonce: str) -> dict[str, Any]:
        if not token or token.count(".") != 2:
            raise GoogleOidcError("invalid_id_token")
        header_b64, payload_b64, sig_b64 = token.split(".")
        try:
            header = json.loads(_b64url_decode(header_b64))
        except Exception:
            raise GoogleOidcError("invalid_id_token")
        if not isinstance(header, dict):
            raise GoogleOidcError("invalid_id_token")
        if header.get("alg") != "RS256":
            raise GoogleOidcError("invalid_id_token")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise GoogleOidcError("invalid_id_token")
        jwks = await self.fetch_jwks()
        key = self._key_for_kid(jwks, kid)
        if key is None:
            jwks = await self.fetch_jwks(force=True)
            key = self._key_for_kid(jwks, kid)
        if key is None:
            raise GoogleOidcError("invalid_id_token")
        try:
            signature = _b64url_decode(sig_b64)
            signed = f"{header_b64}.{payload_b64}".encode("ascii")
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            raise GoogleOidcError("invalid_id_token")
        try:
            claims = json.loads(_b64url_decode(payload_b64))
        except Exception:
            raise GoogleOidcError("invalid_id_token")
        if not isinstance(claims, dict):
            raise GoogleOidcError("invalid_id_token")
        self._validate_claims(claims, audience=audience, nonce=nonce)
        return claims

    def _validate_claims(self, claims: dict[str, Any], *, audience: str, nonce: str) -> None:
        iss = claims.get("iss")
        if not isinstance(iss, str) or iss.rstrip("/") not in allowed_issuers(self.issuer):
            raise GoogleOidcError("invalid_id_token")
        aud = claims.get("aud")
        if isinstance(aud, str):
            aud_ok = hmac.compare_digest(aud, audience)
        elif isinstance(aud, list):
            aud_ok = any(isinstance(a, str) and hmac.compare_digest(a, audience) for a in aud)
        else:
            aud_ok = False
        if not aud_ok:
            raise GoogleOidcError("invalid_id_token")
        azp = claims.get("azp")
        if isinstance(azp, str) and azp and not hmac.compare_digest(azp, audience):
            # azp may name another of our platform clients; still must be ours.
            ours = {self.client_id(p) for p in PLATFORMS if self.client_id(p)}
            if azp not in ours:
                raise GoogleOidcError("invalid_id_token")
        now = time.time()
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or now > float(exp) + JWT_LEEWAY_S:
            raise GoogleOidcError("invalid_id_token")
        iat = claims.get("iat")
        if iat is not None and (not isinstance(iat, (int, float)) or float(iat) > now + JWT_LEEWAY_S):
            raise GoogleOidcError("invalid_id_token")
        nbf = claims.get("nbf")
        if nbf is not None and (not isinstance(nbf, (int, float)) or now + JWT_LEEWAY_S < float(nbf)):
            raise GoogleOidcError("invalid_id_token")
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not nonce or not hmac.compare_digest(token_nonce, nonce):
            raise GoogleOidcError("nonce_mismatch")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip() or len(sub) > 255:
            raise GoogleOidcError("invalid_id_token")

    def public_status(self) -> dict[str, Any]:
        on = self.enabled()
        return {
            "enabled": on,
            "platforms": self.platforms() if on else {p: False for p in PLATFORMS},
            "scopes": list(GOOGLE_SCOPES),
        }


def validate_google_settings(settings) -> None:
    """Refuse reused client IDs in production. Google is otherwise optional."""
    oidc = GoogleOidc(settings)
    if oidc.duplicate_client_ids() and getattr(settings, "production", False):
        raise RuntimeError(
            "Google OAuth client IDs must be unique per platform (web/Android/iOS); "
            "do not reuse one client"
        )
    if oidc.forced_off():
        return
    production = bool(getattr(settings, "production", False))
    for platform in PLATFORMS:
        uri = oidc.redirect_uri(platform)
        if uri and not is_https_redirect_uri(uri) and production:
            raise RuntimeError("GOOGLE_REDIRECT_URI must be an exact-match HTTPS URI")
    if production and oidc.enabled():
        for url in (oidc.auth_url, oidc.token_url, oidc.jwks_url):
            if urlparse(url).scheme.lower() != "https":
                raise RuntimeError("Google OIDC authorization/token/JWKS URLs must be HTTPS")
