"""Phone sign-in / sign-up (dev SMS sink)."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import phonenumbers
import pytest
from phonenumbers import PhoneNumberFormat

from talking.auth.crypto import utcnow
from talking.auth.phone_util import DEFAULT_PHONE_CHANNEL, PhoneError, normalize_phone_channel

from tests.test_auth import ACCOUNTS, _h, _session


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


def test_phone_default_channel_is_sms(make_client):
    c = _client(make_client)
    phone = _phone("GB")
    r = c.post("/api/auth/phone/start", json={"phone": phone}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["channel"] == "sms"
    assert DEFAULT_PHONE_CHANNEL == "sms"


def test_phone_call_me_instead_alias(make_client):
    c = _client(make_client)
    r = _start(c, _phone("FR"), channel="call me instead")
    assert r.status_code == 200, r.text
    assert r.json()["channel"] == "call"


def test_phone_email_channel_rejected(make_client):
    c = _client(make_client)
    r = _start(c, _phone(), channel="email")
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_channel"


def test_production_phone_start_refuses_console_sink(make_client):
    c = _client(make_client)
    c.app_.state.settings.app_env = "production"
    r = _start(c, _phone("CA"))
    assert r.status_code == 503
    assert r.json()["detail"] == "sms_unconfigured"


def test_normalize_phone_channel_sms_default():
    assert normalize_phone_channel(None) == "sms"
    assert normalize_phone_channel("") == "sms"
    assert normalize_phone_channel("SMS") == "sms"
    assert normalize_phone_channel("voice") == "call"
    with pytest.raises(PhoneError, match="invalid_channel"):
        normalize_phone_channel("email")
    with pytest.raises(PhoneError, match="invalid_channel"):
        normalize_phone_channel("whatsapp")


def test_twilio_verify_sends_sms_never_email():
    from talking.auth.providers import TwilioVerifyProvider

    provider = TwilioVerifyProvider("ACtest", "token", "VAtest")
    captured = []

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"sid": "VE123"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    async def _run():
        with patch("talking.auth.providers.httpx.AsyncClient", return_value=mock_client):
            sms = await provider.start("+15555550100", "sms")
            captured.append(mock_client.post.call_args.kwargs["data"]["Channel"])
            email = await provider.start("+15555550100", "email")
            call = await provider.start("+15555550100", "call")
            captured.append(mock_client.post.call_args.kwargs["data"]["Channel"])
            return sms, email, call

    sms, email, call = asyncio.run(_run())
    assert sms.ok and sms.provider == "twilio"
    assert not email.ok
    assert email.error == "invalid_channel"
    assert call.ok
    assert captured == ["sms", "call"]
    for args, kwargs in mock_client.post.call_args_list:
        assert kwargs["data"]["Channel"] in ("sms", "call")
        assert kwargs["data"]["Channel"] != "email"


def test_phone_link_to_existing_account(make_client):
    c = _client(make_client)
    first = _session(c, "US")
    csrf = first.json()["csrf_token"]
    uid = first.json()["user"]["id"]
    phone = _phone("GB")
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
    assert providers == {"phone"}
    assert len(me.json()["methods"]) == 2


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

    other = _session(c, "US")
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
    first = _session(c, "US")
    csrf = first.json()["csrf_token"]
    phone = _phone("GB")
    start = _start(c, phone, csrf=csrf)
    assert start.status_code == 200, start.text
    assert c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    ).status_code == 200
    methods = c.get("/api/auth/methods").json()["methods"]
    assert len(methods) == 2
    extra = methods[1]
    deleted = c.delete(f"/api/auth/methods/{extra['id']}", headers=_h(csrf))
    assert deleted.status_code == 200
    left = c.get("/api/auth/methods").json()["methods"]
    assert len(left) == 1
    assert left[0]["provider"] == "phone"
    last = c.delete(f"/api/auth/methods/{left[0]['id']}", headers=_h(csrf))
    assert last.status_code == 400
    assert last.json()["detail"] == "last_method"
