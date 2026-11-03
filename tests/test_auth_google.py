"""Google Sign-In (OIDC): PKCE, ID-token checks, no email auto-merge."""
from __future__ import annotations

import base64
import json
import logging
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from talking.auth.crypto import utcnow
from talking.auth.google import (
    GOOGLE_SCOPE_STRING,
    GoogleOidc,
    is_https_redirect_uri,
    new_pkce_verifier,
    pkce_s256_challenge,
)
from talking.auth.logfilter import RedactFilter

from tests.test_auth import ACCOUNTS, _anon, _h, _production_accounts_env

WEB_CLIENT = "123-web.apps.googleusercontent.com"
ANDROID_CLIENT = "123-android.apps.googleusercontent.com"
IOS_CLIENT = "123-ios.apps.googleusercontent.com"
WEB_SECRET = "test-web-secret-not-a-real-secret"
REDIRECT = "https://testserver/api/auth/google/callback"
TOKEN_URL = "https://oauth2.googleapis.test/token"
JWKS_URL = "https://www.googleapis.test/oauth2/v3/certs"

GOOGLE = {
    **ACCOUNTS,
    "GOOGLE_CLIENT_ID_WEB": WEB_CLIENT,
    "GOOGLE_CLIENT_SECRET_WEB": WEB_SECRET,
    "GOOGLE_CLIENT_ID_ANDROID": ANDROID_CLIENT,
    "GOOGLE_CLIENT_ID_IOS": IOS_CLIENT,
    "GOOGLE_REDIRECT_URI": REDIRECT,
    "GOOGLE_TOKEN_URL": TOKEN_URL,
    "GOOGLE_JWKS_URL": JWKS_URL,
    "GOOGLE_AUTH_URL": "https://accounts.google.test/o/oauth2/v2/auth",
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_uint(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return _b64url(n.to_bytes(length, "big"))


class FakeGoogle:
    def __init__(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-kid"
        self.issued: dict[str, dict] = {}
        self.exchanges: list[dict] = []
        self.token_status = 200
        self.token_body = None

    def jwks(self) -> dict:
        nums = self.private_key.public_key().public_numbers()
        return {
            "keys": [{
                "kty": "RSA", "alg": "RS256", "use": "sig", "kid": self.kid,
                "n": _b64url_uint(nums.n), "e": _b64url_uint(nums.e),
            }]
        }

    def sign(self, claims: dict, *, kid: str | None = None, alg: str = "RS256") -> str:
        header = {"alg": alg, "typ": "JWT", "kid": kid or self.kid}
        h = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p = _b64url(json.dumps(claims, separators=(",", ":")).encode())
        if alg == "none":
            return f"{h}.{p}."
        sig = self.private_key.sign(
            f"{h}.{p}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256(),
        )
        return f"{h}.{p}.{_b64url(sig)}"

    def issue(
        self, code: str, *, nonce: str, sub: str, aud: str = WEB_CLIENT,
        email: str = "user@example.com", email_verified: bool = True, **extra,
    ) -> None:
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": aud,
            "exp": now + 3600,
            "iat": now,
            "sub": sub,
            "nonce": nonce,
            "email": email,
            "email_verified": email_verified,
        }
        claims.update(extra)
        self.issued[code] = claims

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.startswith(TOKEN_URL):
            form = parse_qs(request.content.decode())
            payload = {k: v[0] if v else "" for k, v in form.items()}
            self.exchanges.append(payload)
            if self.token_body is not None:
                return httpx.Response(self.token_status, json=self.token_body)
            code = payload.get("code", "")
            claims = self.issued.get(code)
            if not claims:
                return httpx.Response(400, json={"error": "invalid_grant"})
            body = {
                "id_token": self.sign(claims),
                "access_token": "ya29.not-for-the-browser",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
            return httpx.Response(self.token_status, json=body)
        if request.method == "GET" and url.startswith(JWKS_URL):
            return httpx.Response(200, json=self.jwks())
        return httpx.Response(404, json={"error": "not_mocked"})

    def install(self, client) -> None:
        client.app_.state.auth.google._transport = httpx.MockTransport(self.handler)


def _client(make_client, **extra):
    env = dict(GOOGLE)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def _json_h(csrf=None):
    h = _h(csrf)
    h["Accept"] = "application/json"
    return h


def _start(c, **body):
    return c.post("/api/auth/google/start", json=body or {}, headers=_h())


def _auth_query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _callback(c, code: str, state: str, **extra):
    params = {"code": code, "state": state, "format": "json"}
    params.update(extra)
    return c.get("/api/auth/google/callback", params=params, headers=_json_h())


def test_google_off_unless_configured(make_client):
    c = make_client(env=ACCOUNTS, base_url="https://testserver")
    st = c.get("/api/auth/google")
    assert st.status_code == 200
    assert st.json()["enabled"] is False
    assert st.json()["platforms"] == {"web": False, "android": False, "ios": False}
    r = c.post("/api/auth/google/start", json={}, headers=_h())
    assert r.status_code == 503
    assert r.json()["detail"] == "google_unconfigured"


def test_google_kill_switch(make_client):
    c = _client(make_client, GOOGLE_OAUTH_ENABLED="0")
    assert c.get("/api/auth/google").json()["enabled"] is False
    r = _start(c)
    assert r.status_code == 503


def test_google_http_redirect_uri_is_off(make_client):
    c = _client(make_client, GOOGLE_REDIRECT_URI="http://testserver/api/auth/google/callback")
    assert c.get("/api/auth/google").json()["enabled"] is False


def test_google_local_mode_accounts_only(make_client):
    c = make_client(env={**GOOGLE, "AUTH_MODE": "local"}, base_url="https://testserver")
    assert c.get("/api/auth/google").json()["enabled"] is False
    r = _start(c)
    assert r.status_code == 400
    assert r.json()["detail"] == "accounts_only"
    cb = c.get(
        "/api/auth/google/callback",
        params={"code": "x", "state": "y", "format": "json"},
        headers=_json_h(),
    )
    assert cb.status_code == 400
    assert cb.json()["detail"] == "accounts_only"


def test_start_authorization_url_pkce_s256_and_scopes(make_client):
    c = _client(make_client)
    r = _start(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "code_verifier" not in body
    assert "nonce" not in body
    assert "id_token" not in body
    url = body["authorization_url"]
    q = _auth_query(url)
    assert q["client_id"] == WEB_CLIENT
    assert q["redirect_uri"] == REDIRECT
    assert q["response_type"] == "code"
    assert q["scope"] == GOOGLE_SCOPE_STRING
    assert q["code_challenge_method"] == "S256"
    assert q["code_challenge"]
    assert q["state"]
    assert q["nonce"]
    assert "access_type" not in q
    assert "include_granted_scopes" not in q
    assert set(q["scope"].split()) == {"openid", "email", "profile"}


def test_platform_uses_separate_client_ids(make_client):
    c = _client(make_client)
    web = _auth_query(_start(c, platform="web").json()["authorization_url"])
    android = _auth_query(_start(c, platform="android").json()["authorization_url"])
    ios = _auth_query(_start(c, platform="ios").json()["authorization_url"])
    assert web["client_id"] == WEB_CLIENT
    assert android["client_id"] == ANDROID_CLIENT
    assert ios["client_id"] == IOS_CLIENT
    assert len({web["client_id"], android["client_id"], ios["client_id"]}) == 3


def test_post_callback_for_native_clients(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("post-code", nonce=q["nonce"], sub="native-sub")
    r = c.post(
        "/api/auth/google/callback",
        json={"code": "post-code", "state": q["state"]},
        headers=_h(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["created"] is True
    assert "ya29." not in r.text
    assert "id_token" not in r.json()


def test_google_login_creates_account(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    started = _start(c)
    q = _auth_query(started.json()["authorization_url"])
    fake.issue("ok-code", nonce=q["nonce"], sub="google-sub-1")
    cb = _callback(c, "ok-code", q["state"])
    assert cb.status_code == 201, cb.text
    body = cb.json()
    assert body["created"] is True
    assert "id_token" not in body
    assert "access_token" not in str(body)
    assert "ya29." not in cb.text
    uid = body["user"]["id"]
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    assert {m["provider"] for m in me.json()["methods"]} == {"google"}
    exchange = fake.exchanges[-1]
    assert exchange["code_verifier"]
    assert exchange["redirect_uri"] == REDIRECT
    assert exchange["client_id"] == WEB_CLIENT
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["client_secret"] == WEB_SECRET


def test_google_second_login_same_sub(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("c1", nonce=q["nonce"], sub="same-sub")
    first = _callback(c, "c1", q["state"])
    uid = first.json()["user"]["id"]
    csrf = first.json()["csrf_token"]
    c.post("/api/auth/logout-all", headers=_h(csrf))
    q2 = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("c2", nonce=q2["nonce"], sub="same-sub")
    second = _callback(c, "c2", q2["state"])
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["user"]["id"] == uid


def test_google_same_email_different_sub_does_not_merge(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("a", nonce=q["nonce"], sub="sub-a", email="shared@example.com")
    a = _callback(c, "a", q["state"])
    uid_a = a.json()["user"]["id"]
    c.post("/api/auth/logout-all", headers=_h(a.json()["csrf_token"]))
    q2 = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("b", nonce=q2["nonce"], sub="sub-b", email="shared@example.com")
    b = _callback(c, "b", q2["state"])
    assert b.json()["created"] is True
    assert b.json()["user"]["id"] != uid_a


def test_google_link_requires_confirm_and_session(make_client):
    c = _client(make_client)
    denied = _start(c, intent="link", confirm=True)
    assert denied.status_code == 401
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    missing = c.post(
        "/api/auth/google/start",
        json={"intent": "link"},
        headers=_h(csrf),
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "confirm_required"


def test_google_link_to_signed_in_account(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    uid = anon.json()["user"]["id"]
    started = c.post(
        "/api/auth/google/start",
        json={"intent": "link", "confirm": True},
        headers=_h(csrf),
    )
    assert started.status_code == 200, started.text
    q = _auth_query(started.json()["authorization_url"])
    fake.issue("link-code", nonce=q["nonce"], sub="google-link-1")
    linked = _callback(c, "link-code", q["state"])
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked"] is True
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    assert {m["provider"] for m in me.json()["methods"]} == {"anonymous", "google"}


def test_google_link_no_auto_merge(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("owner", nonce=q["nonce"], sub="taken-sub")
    owner = _callback(c, "owner", q["state"])
    c.post("/api/auth/logout-all", headers=_h(owner.json()["csrf_token"]))
    other = _anon(c)
    other_csrf = other.json()["csrf_token"]
    other_id = other.json()["user"]["id"]
    started = c.post(
        "/api/auth/google/start",
        json={"intent": "link", "confirm": True},
        headers=_h(other_csrf),
    )
    q2 = _auth_query(started.json()["authorization_url"])
    fake.issue("conflict", nonce=q2["nonce"], sub="taken-sub")
    conflict = _callback(c, "conflict", q2["state"])
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "google_taken"
    assert c.get("/api/auth/me").json()["id"] == other_id


def test_google_link_fresh_session_required(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    sess = next(iter(c.app_.state.auth_store.sessions.values()))
    sess.auth_at = utcnow() - timedelta(minutes=11)
    stale = c.post(
        "/api/auth/google/start",
        json={"intent": "link", "confirm": True},
        headers=_h(csrf),
    )
    assert stale.status_code == 403
    assert stale.json()["detail"] == "fresh_session_required"


def test_invalid_id_token_signature(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake.install(c)

    def bad_sign(claims, **kw):
        header = {"alg": "RS256", "typ": "JWT", "kid": fake.kid}
        h = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p = _b64url(json.dumps(claims, separators=(",", ":")).encode())
        sig = other.sign(f"{h}.{p}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{h}.{p}.{_b64url(sig)}"

    fake.sign = bad_sign  # type: ignore[method-assign]
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("bad-sig", nonce=q["nonce"], sub="x")
    r = _callback(c, "bad-sig", q["state"])
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_id_token"


def test_nonce_mismatch(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("n", nonce="totally-different-nonce", sub="x")
    r = _callback(c, "n", q["state"])
    assert r.status_code == 401
    assert r.json()["detail"] == "nonce_mismatch"


def test_wrong_audience(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("aud", nonce=q["nonce"], sub="x", aud="someone-else.apps.googleusercontent.com")
    r = _callback(c, "aud", q["state"])
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_id_token"


def test_expired_id_token(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("exp", nonce=q["nonce"], sub="x", exp=int(time.time()) - 120)
    r = _callback(c, "exp", q["state"])
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_id_token"


def test_wrong_issuer(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("iss", nonce=q["nonce"], sub="x", iss="https://evil.example")
    r = _callback(c, "iss", q["state"])
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_id_token"


def test_alg_none_rejected(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com", "aud": WEB_CLIENT, "exp": now + 3600,
        "iat": now, "sub": "x", "nonce": q["nonce"],
    }
    fake.token_body = {"id_token": fake.sign(claims, alg="none"), "access_token": "x"}
    r = _callback(c, "any", q["state"])
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_id_token"


def test_state_reuse_rejected(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("once", nonce=q["nonce"], sub="x")
    assert _callback(c, "once", q["state"]).status_code == 201
    again = _callback(c, "once", q["state"])
    assert again.status_code == 401
    assert again.json()["detail"] == "invalid_state"


def test_oauth_denied_consumes_state(make_client):
    c = _client(make_client)
    q = _auth_query(_start(c).json()["authorization_url"])
    denied = _callback(c, "", q["state"], error="access_denied")
    assert denied.status_code == 401
    assert denied.json()["detail"] == "oauth_denied"
    fake = FakeGoogle()
    fake.install(c)
    fake.issue("late", nonce=q["nonce"], sub="x")
    assert _callback(c, "late", q["state"]).status_code == 401


def test_android_token_exchange_has_no_client_secret(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    started = _start(c, platform="android")
    q = _auth_query(started.json()["authorization_url"])
    fake.issue("and", nonce=q["nonce"], sub="droid", aud=ANDROID_CLIENT)
    r = _callback(c, "and", q["state"])
    assert r.status_code == 201, r.text
    assert "client_secret" not in fake.exchanges[-1]


def test_browser_callback_redirects_without_tokens(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("redir", nonce=q["nonce"], sub="browser-user")
    r = c.get(
        "/api/auth/google/callback",
        params={"code": "redir", "state": q["state"]},
        headers=_h(),
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers.get("location") or ""
    assert "auth=ok" in loc
    assert "ya29." not in loc
    assert "id_token" not in loc
    assert r.cookies.get("access") or r.cookies.get("__Host-access")


def test_unverified_email_not_stored(make_client):
    c = _client(make_client)
    fake = FakeGoogle()
    fake.install(c)
    q = _auth_query(_start(c).json()["authorization_url"])
    fake.issue("uv", nonce=q["nonce"], sub="no-mail", email="open@example.com", email_verified=False)
    r = _callback(c, "uv", q["state"])
    assert r.status_code == 201
    ident = next(i for i in c.app_.state.auth_store.identities.values() if i.provider == "google")
    assert ident.email_hash is None
    assert ident.email_enc is None
    assert ident.email_verified is False


def test_jwt_redacted_from_logs():
    filt = RedactFilter(b"salt")
    token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "c2lnbmF0dXJlYmFzZTY0dXJs"
    )
    record = logging.LogRecord(
        name="talking.auth", level=logging.INFO, pathname=__file__, lineno=1,
        msg=f"id_token={token}", args=(), exc_info=None,
    )
    assert filt.filter(record)
    assert token not in record.msg
    assert "[redacted-jwt]" in record.msg


def test_https_redirect_helpers():
    assert is_https_redirect_uri("https://talking.example/api/auth/google/callback")
    assert not is_https_redirect_uri("http://talking.example/api/auth/google/callback")
    assert not is_https_redirect_uri("https://user:pass@talking.example/cb")
    assert not is_https_redirect_uri("https://talking.example/cb#frag")
    v = new_pkce_verifier()
    assert 43 <= len(v) <= 128
    assert pkce_s256_challenge(v) != v


def test_production_duplicate_google_client_ids_rejected(monkeypatch, tmp_path):
    _production_accounts_env(
        monkeypatch, tmp_path,
        SMS_PROVIDER="twilio",
        TWILIO_ACCOUNT_SID="ACtest",
        TWILIO_AUTH_TOKEN="token",
        TWILIO_VERIFY_SERVICE_SID="VAtest",
        GOOGLE_CLIENT_ID_WEB="same-id.apps.googleusercontent.com",
        GOOGLE_CLIENT_ID_ANDROID="same-id.apps.googleusercontent.com",
        GOOGLE_CLIENT_SECRET_WEB="secret",
        GOOGLE_REDIRECT_URI="https://talking.example/api/auth/google/callback",
    )
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="unique per platform"):
        create_app(Settings())


def test_production_http_google_redirect_rejected(monkeypatch, tmp_path):
    _production_accounts_env(
        monkeypatch, tmp_path,
        SMS_PROVIDER="twilio",
        TWILIO_ACCOUNT_SID="ACtest",
        TWILIO_AUTH_TOKEN="token",
        TWILIO_VERIFY_SERVICE_SID="VAtest",
        GOOGLE_CLIENT_ID_WEB=WEB_CLIENT,
        GOOGLE_CLIENT_SECRET_WEB=WEB_SECRET,
        GOOGLE_REDIRECT_URI="http://talking.example/api/auth/google/callback",
    )
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="HTTPS"):
        create_app(Settings())


def test_production_does_not_require_google(monkeypatch, tmp_path):
    _production_accounts_env(
        monkeypatch, tmp_path,
        SMS_PROVIDER="twilio",
        TWILIO_ACCOUNT_SID="ACtest",
        TWILIO_AUTH_TOKEN="token",
        TWILIO_VERIFY_SERVICE_SID="VAtest",
    )
    from talking.auth.http import validate_auth_settings
    from talking.config import Settings
    validate_auth_settings(Settings())


def test_unsupported_platform(make_client):
    c = _client(make_client)
    r = _start(c, platform="desktop")
    assert r.status_code == 400
    assert r.json()["detail"] == "unsupported_platform"


def test_web_without_secret_not_ready(make_client):
    c = _client(
        make_client,
        GOOGLE_CLIENT_SECRET_WEB="",
        GOOGLE_CLIENT_ID_ANDROID="",
        GOOGLE_CLIENT_ID_IOS="",
    )
    assert c.get("/api/auth/google").json()["enabled"] is False


def test_verify_id_token_unit():
    from talking.config import Settings
    fake = FakeGoogle()
    settings = Settings()
    settings.google_jwks_url = JWKS_URL
    settings.google_token_url = TOKEN_URL
    settings.google_issuer = "https://accounts.google.com"
    oidc = GoogleOidc(settings, transport=httpx.MockTransport(fake.handler))
    now = int(time.time())
    nonce = "unit-nonce"
    claims = {
        "iss": "https://accounts.google.com", "aud": WEB_CLIENT,
        "exp": now + 60, "iat": now, "sub": "unit-sub", "nonce": nonce,
    }

    async def _run():
        token = fake.sign(claims)
        return await oidc.verify_id_token(token, audience=WEB_CLIENT, nonce=nonce)

    import asyncio
    out = asyncio.run(_run())
    assert out["sub"] == "unit-sub"
