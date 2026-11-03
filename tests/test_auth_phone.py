"""Phone sign-in / sign-up (dev SMS sink)."""
from __future__ import annotations

from datetime import timedelta

import phonenumbers
from phonenumbers import PhoneNumberFormat

from talking.auth.crypto import utcnow

from tests.test_auth import ACCOUNTS, _anon, _h


def _phone(region="US") -> str:
    n = phonenumbers.example_number(region)
    return phonenumbers.format_number(n, PhoneNumberFormat.E164)


def _client(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def _start(c, phone, channel="sms", csrf=None):
    return c.post(
        "/api/auth/phone/start",
        json={"phone": phone, "channel": channel},
        headers=_h(csrf),
    )


def test_phone_start_verify_happy_path(make_client):
    c = _client(make_client)
    phone = _phone()
    r = _start(c, phone)
    assert r.status_code == 200, r.text
    code = r.json()["debug_code"]
    assert len(code) == 6
    assert "debug_code" in r.json()
    v = c.post("/api/auth/phone/verify", json={"phone": phone, "code": code}, headers=_h())
    assert v.status_code == 200, v.text
    assert v.json()["created"] is True
    uid = v.json()["user"]["id"]
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    methods = [m["provider"] for m in me.json()["methods"]]
    assert "phone" in methods


def test_phone_wrong_code(make_client):
    c = _client(make_client)
    phone = _phone()
    assert _start(c, phone).status_code == 200
    r = c.post("/api/auth/phone/verify", json={"phone": phone, "code": "000000"}, headers=_h())
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_code"


def test_phone_expiry(make_client):
    c = _client(make_client)
    phone = _phone()
    r = _start(c, phone)
    code = r.json()["debug_code"]
    ch = next(iter(c.app_.state.auth_store.challenges.values()))
    ch.expires_at = utcnow() - timedelta(seconds=1)
    bad = c.post("/api/auth/phone/verify", json={"phone": phone, "code": code}, headers=_h())
    assert bad.status_code == 401
    assert bad.json()["detail"] == "code_expired"


def test_phone_attempts_exhausted(make_client):
    c = _client(make_client)
    phone = _phone()
    assert _start(c, phone).status_code == 200
    for _ in range(5):
        r = c.post("/api/auth/phone/verify", json={"phone": phone, "code": "000000"}, headers=_h())
    assert r.status_code == 429
    assert r.json()["detail"] == "attempts_exhausted"


def test_phone_rate_limit(make_client):
    c = _client(make_client)
    phone = _phone()
    assert _start(c, phone).status_code == 200
    cool = _start(c, phone)
    assert cool.status_code == 429
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
    assert _start(c, phone).status_code == 200
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
    fourth = _start(c, phone)
    assert fourth.status_code == 429


def test_phone_call_channel(make_client):
    c = _client(make_client)
    phone = _phone()
    r = _start(c, phone, channel="call")
    assert r.status_code == 200, r.text
    assert r.json()["channel"] == "call"
    v = c.post(
        "/api/auth/phone/verify",
        json={"phone": phone, "code": r.json()["debug_code"]},
        headers=_h(),
    )
    assert v.status_code == 200


def test_phone_link_to_existing_account(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    uid = anon.json()["user"]["id"]
    phone = _phone()
    start = _start(c, phone, csrf=csrf)
    assert start.status_code == 200, start.text
    linked = c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    )
    assert linked.status_code == 200, linked.text
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    providers = {m["provider"] for m in me.json()["methods"]}
    assert providers == {"anonymous", "phone"}


def test_phone_no_auto_merge(make_client):
    c = _client(make_client)
    phone = _phone("GB")
    start = _start(c, phone)
    v = c.post(
        "/api/auth/phone/verify",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(),
    )
    assert v.status_code == 200
    owner = v.json()["user"]["id"]
    csrf = v.json()["csrf_token"]
    c.post("/api/auth/logout-all", headers=_h(csrf))

    other = _anon(c)
    other_csrf = other.json()["csrf_token"]
    other_id = other.json()["user"]["id"]
    assert other_id != owner
    c.app_.state.auth_limiter._hits.clear()
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
        ch.consumed_at = utcnow()
    start2 = _start(c, phone, csrf=other_csrf)
    assert start2.status_code == 200, start2.text
    conflict = c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start2.json()["debug_code"]},
        headers=_h(other_csrf),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "phone_taken"
    me = c.get("/api/auth/me")
    assert me.json()["id"] == other_id


def test_phone_country_allowlist(make_client):
    c = _client(make_client, SMS_COUNTRY_ALLOWLIST="DE")
    r = _start(c, _phone("US"))
    assert r.status_code == 403
    assert r.json()["detail"] == "country_not_allowed"


def test_phone_unlink_method(make_client):
    c = _client(make_client)
    anon = _anon(c)
    csrf = anon.json()["csrf_token"]
    phone = _phone()
    start = _start(c, phone, csrf=csrf)
    assert start.status_code == 200, start.text
    assert c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    ).status_code == 200
    methods = c.get("/api/auth/methods").json()["methods"]
    phone_m = next(m for m in methods if m["provider"] == "phone")
    deleted = c.delete(f"/api/auth/methods/{phone_m['id']}", headers=_h(csrf))
    assert deleted.status_code == 200
    left = {m["provider"] for m in c.get("/api/auth/methods").json()["methods"]}
    assert left == {"anonymous"}
