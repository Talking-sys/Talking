"""Sign in with Apple: PKCE, form_post, JWKS, no auto-merge, S2S notifications."""
from __future__ import annotations

import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from talking.auth.apple import (
    is_private_relay,
    nickname_from_apple_name,
    nonce_matches,
    parse_apple_user_form,
    pkce_pair,
)
from talking.auth.crypto import utcnow
from talking.auth.jwt_es256 import encode_es256, public_jwk, verify_es256
from talking.auth.http import validate_auth_settings
from talking.config import Settings

from tests.test_auth import ACCOUNTS, _anon, _h, _production_accounts_env

CLIENT_ID = "com.talking.test.web"
TEAM_ID = "TEAM123456"
KEY_ID = "KEYID12345"
REDIRECT = "https://testserver/api/auth/apple/callback"
APPLE_KID = "APPLEKID1"


def _ec_pem():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem, key


CLIENT_PEM, CLIENT_KEY = _ec_pem()
_APPLE_PEM, APPLE_KEY = _ec_pem()

APPLE_ENV = {
    "APPLE_CLIENT_ID": CLIENT_ID,
    "APPLE_TEAM_ID": TEAM_ID,
    "APPLE_KEY_ID": KEY_ID,
    "APPLE_PRIVATE_KEY": CLIENT_PEM,
    "APPLE_REDIRECT_URI": REDIRECT,
}


JSON_H = {**_h(), "Accept": "application/json"}


class FakeApple:
    """httpx transport that stands in for Apple JWKS + token endpoints."""

    def __init__(self):
        self.sub = "001234.apple-test-subject"
        self.email = "relay-user@privaterelay.appleid.com"
        self.nonce = ""
        self.private_relay = True
        self.email_verified = True
        self.last_token: dict[str, list[str]] = {}
        self.force_status = 200
        self.jwks_kid = APPLE_KID
        self.transport = httpx.MockTransport(self._handler)

    def id_token(self, **extra) -> str:
        now = int(time.time())
        payload = {
            "iss": "https://appleid.apple.com",
            "aud": CLIENT_ID,
            "exp": now + 600,
            "iat": now,
            "sub": self.sub,
            "nonce": self.nonce,
            "nonce_supported": True,
            "email": self.email,
            "email_verified": "true" if self.email_verified else "false",
            "is_private_email": "true" if self.private_relay else "false",
        }
        payload.update(extra)
        return encode_es256(payload, APPLE_KEY, kid=self.jwks_kid)

    def notification(self, event_type: str, sub: str | None = None) -> str:
        now = int(time.time())
        events = json.dumps({
            "type": event_type,
            "sub": sub or self.sub,
            "event_time": now,
        })
        payload = {
            "iss": "https://appleid.apple.com",
            "aud": CLIENT_ID,
            "exp": now + 600,
            "iat": now,
            "events": events,
        }
        return encode_es256(payload, APPLE_KEY, kid=APPLE_KID)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/keys") or path.endswith("/keys"):
            return httpx.Response(200, json={"keys": [public_jwk(APPLE_KEY, APPLE_KID)]})
        if path.endswith("/auth/token") or path.endswith("/token"):
            self.last_token = parse_qs(request.content.decode())
            if self.force_status != 200:
                return httpx.Response(self.force_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={
                "id_token": self.id_token(),
                "access_token": "opaque-access",
                "token_type": "bearer",
            })
        return httpx.Response(404)


def _client(make_client, fake: FakeApple | None = None, **extra):
    env = dict(ACCOUNTS)
    env.update(APPLE_ENV)
    env.update(extra)
    c = make_client(env=env, base_url="https://testserver")
    if fake is not None and c.app_.state.auth.apple is not None:
        c.app_.state.auth.apple.transport = fake.transport
        c.app_.state.auth.apple._jwks = None
    return c


def _start(c, **kwargs):
    """JSON start so TestClient does not follow a 302 to appleid.apple.com."""
    r = c.get("/api/auth/apple/start", headers=JSON_H, **kwargs)
    return r


def _pending(c):
    ch = next(
        x for x in c.app_.state.auth_store.challenges.values()
        if x.kind == "apple_oauth" and x.consumed_at is None
    )
    packed = json.loads(ch.provider_ref)
    return packed["n"], packed["v"], ch


def _state_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def test_helpers():
    assert is_private_relay("abc@privaterelay.appleid.com")
    assert not is_private_relay("ada@example.com")
    assert nickname_from_apple_name({"firstName": "Ada", "lastName": "Lovelace"}) == "Ada"
    assert nickname_from_apple_name({}) is None
    v, ch = pkce_pair()
    assert len(v) >= 43 and ch
    assert nonce_matches("abc", "abc")
    import hashlib
    digest = hashlib.sha256(b"abc").hexdigest()
    assert nonce_matches("abc", digest)
    assert not nonce_matches("abc", "xyz")
    assert parse_apple_user_form('{"name":{"firstName":"Ada"}}')["name"]["firstName"] == "Ada"


def test_jwt_es256_roundtrip():
    now = int(time.time())
    token = encode_es256(
        {"iss": "https://appleid.apple.com", "aud": CLIENT_ID, "exp": now + 60, "iat": now, "sub": "s"},
        APPLE_KEY, kid=APPLE_KID,
    )
    claims = verify_es256(
        token, {"keys": [public_jwk(APPLE_KEY, APPLE_KID)]},
        audience=CLIENT_ID, issuer="https://appleid.apple.com",
    )
    assert claims["sub"] == "s"
    with pytest.raises(Exception):
        verify_es256(
            token, {"keys": [public_jwk(APPLE_KEY, APPLE_KID)]},
            audience="other", issuer="https://appleid.apple.com",
        )


def test_local_mode_refuses_apple_start(make_client):
    env = dict(APPLE_ENV)
    env["AUTH_MODE"] = "local"
    env["APP_SECRET_KEY"] = ACCOUNTS["APP_SECRET_KEY"]
    c = make_client(env=env, base_url="https://testserver")
    r = c.get("/api/auth/apple/start", headers=JSON_H)
    assert r.status_code == 400
    assert r.json()["detail"] == "accounts_only"


def test_apple_off_unless_configured(make_client):
    c = make_client(env=dict(ACCOUNTS), base_url="https://testserver")
    r = c.get("/api/auth/apple/start", headers=JSON_H)
    assert r.status_code == 503
    assert r.json()["detail"] == "apple_unconfigured"
    providers = c.get("/api/auth/providers").json()["providers"]
    assert providers["apple"] is False
    assert providers["phone"] is True


def test_partial_apple_config_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_MODE", "accounts")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.delenv("APPLE_TEAM_ID", raising=False)
    monkeypatch.delenv("APPLE_KEY_ID", raising=False)
    monkeypatch.delenv("APPLE_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("APPLE_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("APPLE_REDIRECT_URI", raising=False)
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="partially configured"):
        create_app(Settings())


def test_redirect_uri_must_be_https(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_MODE", "accounts")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("APPLE_TEAM_ID", TEAM_ID)
    monkeypatch.setenv("APPLE_KEY_ID", KEY_ID)
    monkeypatch.setenv("APPLE_PRIVATE_KEY", CLIENT_PEM)
    monkeypatch.setenv("APPLE_REDIRECT_URI", "http://localhost/api/auth/apple/callback")
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="https"):
        create_app(Settings())


def test_production_does_not_require_apple(monkeypatch, tmp_path):
    _production_accounts_env(
        monkeypatch, tmp_path,
        SMS_PROVIDER="twilio",
        TWILIO_ACCOUNT_SID="ACtest",
        TWILIO_AUTH_TOKEN="token",
        TWILIO_VERIFY_SERVICE_SID="VAtest",
    )
    for key in (
        "APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID",
        "APPLE_PRIVATE_KEY", "APPLE_PRIVATE_KEY_FILE", "APPLE_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    validate_auth_settings(Settings())


def test_start_redirects_with_pkce_state_nonce_form_post(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    browser = c.get("/api/auth/apple/start", headers=_h(), follow_redirects=False)
    assert browser.status_code == 302, browser.text
    loc = browser.headers["location"]
    r = _start(c)
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://appleid.apple.com/auth/authorize")
    q = parse_qs(urlparse(loc).query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == [REDIRECT]
    assert q["response_type"] == ["code"]
    assert q["response_mode"] == ["form_post"]
    assert q["scope"] == ["name email"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"][0] and q["nonce"][0] and q["code_challenge"][0]
    assert c.get("/api/auth/providers").json()["providers"]["apple"] is True


def test_register_and_signin_form_post(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    nonce, verifier, _ch = _pending(c)
    fake.nonce = nonce
    user_json = json.dumps({
        "name": {"firstName": "Ada", "lastName": "Lovelace"},
        "email": fake.email,
    })
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "auth-code-1", "state": _state_from(loc), "user": user_json},
        headers={**JSON_H, "Origin": "https://appleid.apple.com", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["private_relay"] is True
    assert body["user"]["nickname"] == "Ada"
    uid = body["user"]["id"]
    token_body = fake.last_token
    assert token_body["grant_type"] == ["authorization_code"]
    assert token_body["redirect_uri"] == [REDIRECT]
    assert token_body["code_verifier"] == [verifier]
    assert token_body["client_id"] == [CLIENT_ID]
    secret = token_body["client_secret"][0]
    secret_claims = verify_es256(
        secret, {"keys": [public_jwk(CLIENT_KEY, KEY_ID)]},
        audience="https://appleid.apple.com", issuer=TEAM_ID,
    )
    assert secret_claims["sub"] == CLIENT_ID
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    methods = [m["provider"] for m in me.json()["methods"]]
    assert methods == ["apple"]

    # Second visit: Apple omits name (first-authorization only). Nickname stays.
    c.post("/api/auth/logout-all", headers=_h(body["csrf_token"]))
    loc2 = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    r2 = c.post(
        "/api/auth/apple/callback",
        data={"code": "auth-code-2", "state": _state_from(loc2)},
        headers=JSON_H,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["created"] is False
    assert r2.json()["user"]["id"] == uid
    assert r2.json()["user"]["nickname"] == "Ada"


def test_no_auto_merge_on_email(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    )
    owner = r.json()["user"]["id"]
    email_hash = next(iter(c.app_.state.auth_store.identities.values())).email_hash
    assert email_hash

    # A different Apple sub with the same private-relay-looking email must not merge.
    fake.sub = "009999.other-apple-subject"
    loc2 = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    r2 = c.post(
        "/api/auth/apple/callback",
        data={"code": "c2", "state": _state_from(loc2)},
        headers=JSON_H,
    )
    assert r2.status_code == 200
    assert r2.json()["created"] is True
    assert r2.json()["user"]["id"] != owner


def test_link_to_existing_account(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    uid = anon.json()["user"]["id"]
    start = c.post(
        "/api/auth/apple/start",
        json={"intent": "link"},
        headers={**JSON_H, "X-CSRF-Token": csrf},
    )
    assert start.status_code == 200, start.text
    fake.nonce = _pending(c)[0]
    linked = c.post(
        "/api/auth/apple/callback",
        data={"code": "link-code", "state": _state_from(start.json()["url"])},
        headers={**JSON_H, "X-CSRF-Token": csrf},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked"] is True
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    assert {m["provider"] for m in me.json()["methods"]} == {"anonymous", "apple"}


def test_link_requires_post(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    anon = _anon(c)
    r = c.get("/api/auth/apple/start?intent=link", headers={**JSON_H, "X-CSRF-Token": anon.json()["csrf_token"]})
    assert r.status_code == 405


def test_no_auto_merge_on_link(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    first = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    )
    owner = first.json()["user"]["id"]
    c.post("/api/auth/logout-all", headers=_h(first.json()["csrf_token"]))

    other = _anon(c)
    other_id = other.json()["user"]["id"]
    csrf = other.json()["csrf_token"]
    start = c.post(
        "/api/auth/apple/start",
        json={"intent": "link"},
        headers={**JSON_H, "X-CSRF-Token": csrf},
    )
    fake.nonce = _pending(c)[0]
    conflict = c.post(
        "/api/auth/apple/callback",
        data={"code": "c2", "state": _state_from(start.json()["url"])},
        headers=JSON_H,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "apple_taken"
    me = c.get("/api/auth/me")
    assert me.json()["id"] == other_id
    assert other_id != owner


def test_invalid_state_and_replay(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    state = _state_from(loc)
    bad = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": "not-the-state"},
        headers=JSON_H,
    )
    assert bad.status_code == 401
    ok = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": state},
        headers=JSON_H,
    )
    assert ok.status_code == 200
    replay = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": state},
        headers=JSON_H,
    )
    assert replay.status_code == 401


def test_expired_challenge(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    ch = _pending(c)[2]
    ch.expires_at = utcnow() - timedelta(seconds=1)
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_state"


def test_invalid_id_token_nonce(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    _pending(c)
    fake.nonce = "wrong-nonce"
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_nonce"


def test_invalid_id_token_signature(make_client):
    fake = FakeApple()
    other_pem, other_key = _ec_pem()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]

    async def _bad_exchange(code, verifier):
        now = int(time.time())
        token = encode_es256(
            {
                "iss": "https://appleid.apple.com", "aud": CLIENT_ID,
                "exp": now + 600, "iat": now, "sub": fake.sub, "nonce": fake.nonce,
                "nonce_supported": True,
            },
            other_key, kid=APPLE_KID,
        )
        return {"id_token": token}

    c.app_.state.auth.apple.exchange_code = _bad_exchange  # type: ignore[method-assign]
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_id_token"


def test_form_post_csrf_exempt(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers={
            "Accept": "application/json",
            "Origin": "https://appleid.apple.com",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert r.status_code == 200, r.text


def test_callback_browser_redirects(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers={"Origin": "https://appleid.apple.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "auth=ok" in r.headers["location"]
    assert r.cookies.get("access") or r.cookies.get("__Host-access")


def test_notifications_consent_revoked_unlinks(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    start = c.post(
        "/api/auth/apple/start", json={"intent": "link"},
        headers={**JSON_H, "X-CSRF-Token": csrf},
    )
    fake.nonce = _pending(c)[0]
    assert c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(start.json()["url"])},
        headers=JSON_H,
    ).status_code == 200
    providers = {m["provider"] for m in c.get("/api/auth/methods").json()["methods"]}
    assert "apple" in providers
    note = c.post(
        "/api/auth/apple/notifications",
        json={"payload": fake.notification("consent-revoked")},
        headers={"Origin": "https://appleid.apple.com", "Sec-Fetch-Site": "cross-site"},
    )
    assert note.status_code == 200, note.text
    left = {m["provider"] for m in c.get("/api/auth/methods").json()["methods"]}
    assert left == {"anonymous"}


def test_notifications_account_delete_last_method(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    r = c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    )
    uid = r.json()["user"]["id"]
    note = c.post(
        "/api/auth/apple/notifications",
        json={"payload": fake.notification("account-delete")},
    )
    assert note.status_code == 200
    user = c.app_.state.auth_store.users[uid]
    assert user.status == "deleting"
    assert c.get("/api/auth/me").status_code == 401


def test_notifications_invalid_jwt(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    r = c.post("/api/auth/apple/notifications", json={"payload": "not.a.jwt"})
    assert r.status_code == 401


def test_email_disabled_clears_stored_email(make_client):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    assert c.post(
        "/api/auth/apple/callback",
        data={"code": "c1", "state": _state_from(loc)},
        headers=JSON_H,
    ).status_code == 200
    ident = next(iter(c.app_.state.auth_store.identities.values()))
    assert ident.email_enc
    note = c.post(
        "/api/auth/apple/notifications",
        json={"payload": fake.notification("email-disabled")},
    )
    assert note.status_code == 200
    ident = next(iter(c.app_.state.auth_store.identities.values()))
    assert ident.email_enc is None
    assert ident.email_verified is False


def test_apple_does_not_log_email(make_client, caplog):
    fake = FakeApple()
    c = _client(make_client, fake)
    loc = _start(c).json()["url"]
    fake.nonce = _pending(c)[0]
    with caplog.at_level("INFO"):
        c.post(
            "/api/auth/apple/callback",
            data={"code": "c1", "state": _state_from(loc), "user": json.dumps({"email": fake.email})},
            headers=JSON_H,
        )
    joined = " ".join(r.message for r in caplog.records)
    assert fake.email not in joined
    assert "privaterelay" not in joined
