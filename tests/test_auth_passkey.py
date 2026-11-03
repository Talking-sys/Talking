"""WebAuthn passkeys: register, sign-in, clone detection, last-method guard."""
from __future__ import annotations

from datetime import timedelta

from talking.auth.crypto import utcnow
from tests.test_auth import ACCOUNTS, _anon, _h
from tests.webauthn_fake import FakeAuthenticator

ORIGIN = "https://testserver"


def _client(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url=ORIGIN)


def _register(c, csrf, fake=None):
    fake = fake or FakeAuthenticator()
    opts = c.post("/api/auth/passkey/register/options", headers=_h(csrf))
    assert opts.status_code == 200, opts.text
    body = opts.json()
    cred = fake.create(body["publicKey"], ORIGIN)
    fin = c.post(
        "/api/auth/passkey/register",
        json={"challenge_id": body["challenge_id"], "credential": cred},
        headers=_h(csrf),
    )
    return fake, body, fin


def test_passkey_register_and_sign_in(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    uid = anon.json()["user"]["id"]
    fake, _, fin = _register(c, csrf)
    assert fin.status_code == 200, fin.text
    assert fin.json()["method"]["provider"] == "passkey"
    methods = c.get("/api/auth/methods").json()["methods"]
    providers = {m["provider"] for m in methods}
    assert providers == {"anonymous", "passkey"}
    c.post("/api/auth/logout", headers=_h(csrf))
    assert c.get("/api/auth/me").status_code == 401

    opts = c.post("/api/auth/passkey/authenticate/options", headers=_h())
    assert opts.status_code == 200, opts.text
    assertion = fake.get(opts.json()["publicKey"], ORIGIN, uid)
    auth = c.post(
        "/api/auth/passkey/authenticate",
        json={"challenge_id": opts.json()["challenge_id"], "credential": assertion},
        headers=_h(),
    )
    assert auth.status_code == 200, auth.text
    assert auth.json()["user"]["id"] == uid
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == uid
    assert me.json()["csrf_token"]


def test_passkey_local_mode_refused(make_client):
    c = make_client()
    r = c.post("/api/auth/passkey/authenticate/options", headers=_h())
    assert r.status_code == 400
    assert r.json()["detail"] == "accounts_only"


def test_passkey_register_requires_session(make_client):
    c = _client(make_client)
    r = c.post("/api/auth/passkey/register/options", headers=_h())
    assert r.status_code == 401


def test_passkey_register_needs_csrf(make_client):
    c = _client(make_client)
    _anon(c)
    r = c.post("/api/auth/passkey/register/options", headers=_h(origin=ORIGIN))
    assert r.status_code == 403


def test_passkey_fresh_session_required(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    rec = next(iter(c.app_.state.auth_store.sessions.values()))
    rec.auth_at = utcnow() - timedelta(minutes=11)
    r = c.post("/api/auth/passkey/register/options", headers=_h(csrf))
    assert r.status_code == 403
    assert r.json()["detail"] == "fresh_session_required"


def test_passkey_challenge_expiry(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    fake = FakeAuthenticator()
    opts = c.post("/api/auth/passkey/register/options", headers=_h(csrf))
    body = opts.json()
    ch = c.app_.state.auth_store.challenges[body["challenge_id"]]
    ch.expires_at = utcnow() - timedelta(seconds=1)
    cred = fake.create(body["publicKey"], ORIGIN)
    fin = c.post(
        "/api/auth/passkey/register",
        json={"challenge_id": body["challenge_id"], "credential": cred},
        headers=_h(csrf),
    )
    assert fin.status_code == 400
    assert fin.json()["detail"] == "challenge_expired"


def test_passkey_challenge_single_use(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    fake, body, fin = _register(c, csrf)
    assert fin.status_code == 200, fin.text
    again = c.post(
        "/api/auth/passkey/register",
        json={"challenge_id": body["challenge_id"], "credential": fake.create(body["publicKey"], ORIGIN)},
        headers=_h(csrf),
    )
    assert again.status_code == 400
    assert again.json()["detail"] == "invalid_challenge"


def test_passkey_unknown_credential_rejected(make_client):
    c = _client(make_client)
    fake = FakeAuthenticator()
    opts = c.post("/api/auth/passkey/authenticate/options", headers=_h())
    assertion = fake.get(opts.json()["publicKey"], ORIGIN, "00000000-0000-0000-0000-000000000000")
    r = c.post(
        "/api/auth/passkey/authenticate",
        json={"challenge_id": opts.json()["challenge_id"], "credential": assertion},
        headers=_h(),
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_passkey"


def test_passkey_wrong_origin_rejected(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    fake = FakeAuthenticator()
    opts = c.post("/api/auth/passkey/register/options", headers=_h(csrf))
    body = opts.json()
    cred = fake.create(body["publicKey"], "https://evil.example")
    fin = c.post(
        "/api/auth/passkey/register",
        json={"challenge_id": body["challenge_id"], "credential": cred},
        headers=_h(csrf),
    )
    assert fin.status_code == 401
    assert fin.json()["detail"] == "invalid_passkey"


def test_passkey_clone_detection_blocks_sign_in(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    uid = anon.json()["user"]["id"]
    fake, _, fin = _register(c, csrf)
    assert fin.status_code == 200, fin.text
    c.post("/api/auth/logout", headers=_h(csrf))

    opts = c.post("/api/auth/passkey/authenticate/options", headers=_h())
    assertion = fake.get(opts.json()["publicKey"], ORIGIN, uid, sign_count=1)
    first = c.post(
        "/api/auth/passkey/authenticate",
        json={"challenge_id": opts.json()["challenge_id"], "credential": assertion},
        headers=_h(),
    )
    assert first.status_code == 200, first.text
    csrf2 = first.json()["csrf_token"]
    c.post("/api/auth/logout", headers=_h(csrf2))

    opts2 = c.post("/api/auth/passkey/authenticate/options", headers=_h())
    cloned = fake.get(opts2.json()["publicKey"], ORIGIN, uid, sign_count=1)
    bad = c.post(
        "/api/auth/passkey/authenticate",
        json={"challenge_id": opts2.json()["challenge_id"], "credential": cloned},
        headers=_h(),
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "cloned_credential"

    opts3 = c.post("/api/auth/passkey/authenticate/options", headers=_h())
    later = fake.get(opts3.json()["publicKey"], ORIGIN, uid, sign_count=2)
    still = c.post(
        "/api/auth/passkey/authenticate",
        json={"challenge_id": opts3.json()["challenge_id"], "credential": later},
        headers=_h(),
    )
    assert still.status_code == 401
    assert still.json()["detail"] == "cloned_credential"
    methods = _client_methods_after_anon_recover(c, anon.json()["recovery_code"])
    passkey = next(m for m in methods if m["provider"] == "passkey")
    assert passkey.get("cloned") is True


def _client_methods_after_anon_recover(c, code):
    rec = c.post("/api/auth/recover", json={"code": code}, headers=_h())
    assert rec.status_code == 200, rec.text
    return c.get("/api/auth/methods").json()["methods"]


def test_passkey_cannot_remove_last_method(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    _, _, fin = _register(c, csrf)
    assert fin.status_code == 200, fin.text
    methods = c.get("/api/auth/methods").json()["methods"]
    anon_m = next(m for m in methods if m["provider"] == "anonymous")
    assert c.delete(f"/api/auth/methods/{anon_m['id']}", headers=_h(csrf)).status_code == 200
    left = c.get("/api/auth/methods").json()["methods"]
    assert [m["provider"] for m in left] == ["passkey"]
    pk = left[0]
    denied = c.delete(f"/api/auth/methods/{pk['id']}", headers=_h(csrf))
    assert denied.status_code == 400
    assert denied.json()["detail"] == "last_method"


def test_passkey_unlink_when_another_method_exists(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    _, _, fin = _register(c, csrf)
    assert fin.status_code == 200, fin.text
    methods = c.get("/api/auth/methods").json()["methods"]
    pk = next(m for m in methods if m["provider"] == "passkey")
    deleted = c.delete(f"/api/auth/methods/{pk['id']}", headers=_h(csrf))
    assert deleted.status_code == 200
    left = {m["provider"] for m in c.get("/api/auth/methods").json()["methods"]}
    assert left == {"anonymous"}
    assert c.app_.state.auth_store.credentials == {}


def test_passkey_second_device_is_another_method(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    _, _, first = _register(c, csrf)
    assert first.status_code == 200, first.text
    _, _, second = _register(c, csrf, FakeAuthenticator())
    assert second.status_code == 200, second.text
    methods = c.get("/api/auth/methods").json()["methods"]
    assert sum(1 for m in methods if m["provider"] == "passkey") == 2


def test_passkey_rp_from_public_origin(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    opts = c.post("/api/auth/passkey/register/options", headers=_h(csrf))
    pk = opts.json()["publicKey"]
    assert pk["rp"]["id"] == "testserver"
    assert opts.json()["expires_in"] == 120
