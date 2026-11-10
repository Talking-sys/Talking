"""Passwordless email sign-in / sign-up (OTP + magic link, dev mail sink)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from talking.auth.crypto import decrypt_pii, utcnow
from talking.auth.email_util import DEFAULT_EMAIL_CHANNEL, EmailError, normalize_email_channel, parse_email

from tests.test_auth import ACCOUNTS, _h, _session


def _client(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def _start(c, email, channel=None, csrf=None):
    body = {"email": email}
    if channel is not None:
        body["channel"] = channel
    return c.post("/api/auth/email/start", json=body, headers=_h(csrf))


def test_email_start_verify_happy_path(make_client):
    c = _client(make_client)
    email = "User.Name+tag@Example.COM"
    r = _start(c, email)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "both"
    assert body["expires_in"] == 600
    code = body["debug_code"]
    assert len(code) == 6
    v = c.post("/api/auth/email/verify", json={"email": email, "code": code}, headers=_h())
    assert v.status_code == 200, v.text
    assert v.json()["created"] is True
    uid = v.json()["user"]["id"]
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    methods = [m["provider"] for m in me.json()["methods"]]
    assert "email" in methods
    csrf = v.json()["csrf_token"]
    again = c.post("/api/auth/email/verify", json={"email": email, "code": code}, headers=_h(csrf))
    assert again.status_code == 401


def test_email_same_address_signs_into_same_user(make_client):
    c = _client(make_client)
    email = "same@example.com"
    first = _start(c, email)
    v1 = c.post(
        "/api/auth/email/verify",
        json={"email": email, "code": first.json()["debug_code"]},
        headers=_h(),
    )
    uid = v1.json()["user"]["id"]
    csrf = v1.json()["csrf_token"]
    c.post("/api/auth/logout-all", headers=_h(csrf))
    c.app_.state.auth_limiter._hits.clear()
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
        ch.consumed_at = utcnow()
    second = _start(c, email)
    v2 = c.post(
        "/api/auth/email/verify",
        json={"email": email, "code": second.json()["debug_code"]},
        headers=_h(),
    )
    assert v2.status_code == 200, v2.text
    assert v2.json()["created"] is False
    assert v2.json()["user"]["id"] == uid


def test_email_magic_link_post_and_get(make_client):
    c = _client(make_client)
    email = "link@example.com"
    r = _start(c, email, channel="link")
    assert r.status_code == 200, r.text
    assert "debug_code" not in r.json()
    token = r.json()["debug_token"]
    v = c.post("/api/auth/email/consume", json={"token": token}, headers=_h())
    assert v.status_code == 200, v.text
    assert v.json()["created"] is True
    uid = v.json()["user"]["id"]
    assert c.get("/api/auth/me").json()["id"] == uid
    reused = c.post(
        "/api/auth/email/consume", json={"token": token}, headers=_h(v.json()["csrf_token"]),
    )
    assert reused.status_code == 401


def test_email_magic_link_get_sets_cookies(make_client):
    c = _client(make_client)
    start = _start(c, "click@example.com", channel="link")
    assert start.status_code == 200, start.text
    token = start.json()["debug_token"]
    bounced = c.get(
        f"/api/auth/email/consume?token={token}",
        headers=_h(),
        follow_redirects=False,
    )
    assert bounced.status_code == 303
    assert bounced.headers["location"] == "/"
    set_cookie = bounced.headers.get("set-cookie", "")
    assert "access=" in set_cookie
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"]


def test_email_code_consumes_magic_link(make_client):
    c = _client(make_client)
    email = "both@example.com"
    r = _start(c, email, channel="both")
    code = r.json()["debug_code"]
    token = r.json()["debug_token"]
    v = c.post("/api/auth/email/verify", json={"email": email, "code": code}, headers=_h())
    assert v.status_code == 200
    dead = c.post(
        "/api/auth/email/consume", json={"token": token}, headers=_h(v.json()["csrf_token"]),
    )
    assert dead.status_code == 401


def test_email_wrong_code(make_client):
    c = _client(make_client)
    email = "wrong@example.com"
    assert _start(c, email, channel="code").status_code == 200
    r = c.post("/api/auth/email/verify", json={"email": email, "code": "000000"}, headers=_h())
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_code"


def test_email_expiry(make_client):
    c = _client(make_client)
    email = "exp@example.com"
    r = _start(c, email, channel="code")
    code = r.json()["debug_code"]
    for ch in c.app_.state.auth_store.challenges.values():
        ch.expires_at = utcnow() - timedelta(seconds=1)
    bad = c.post("/api/auth/email/verify", json={"email": email, "code": code}, headers=_h())
    assert bad.status_code == 401
    assert bad.json()["detail"] == "code_expired"


def test_email_attempts_exhausted(make_client):
    c = _client(make_client)
    email = "tries@example.com"
    assert _start(c, email, channel="code").status_code == 200
    for _ in range(5):
        r = c.post("/api/auth/email/verify", json={"email": email, "code": "000000"}, headers=_h())
    assert r.status_code == 429
    assert r.json()["detail"] == "attempts_exhausted"


def test_email_rate_limit(make_client):
    c = _client(make_client)
    email = "rate@example.com"
    assert _start(c, email, channel="code").status_code == 200
    cool = _start(c, email, channel="code")
    assert cool.status_code == 429
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
    assert _start(c, email, channel="code").status_code == 200
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
    fourth = _start(c, email, channel="code")
    assert fourth.status_code == 429


def test_email_default_channel_is_both():
    assert DEFAULT_EMAIL_CHANNEL == "both"
    assert normalize_email_channel(None) == "both"
    assert normalize_email_channel("code") == "code"
    assert normalize_email_channel("magic-link") == "link"
    with pytest.raises(EmailError, match="invalid_channel"):
        normalize_email_channel("sms")


def test_parse_email_normalizes_and_rejects_injection():
    assert parse_email("  Foo.Bar@Example.COM ") == "foo.bar@example.com"
    assert parse_email("user+tag@example.com") == "user+tag@example.com"
    with pytest.raises(EmailError, match="email_required"):
        parse_email("")
    with pytest.raises(EmailError, match="invalid_email"):
        parse_email("not-an-email")
    with pytest.raises(EmailError, match="invalid_email"):
        parse_email("user@example.com\nBcc: evil@example.com")
    with pytest.raises(EmailError, match="invalid_email"):
        parse_email("user@localhost")


def test_production_email_start_refuses_console_sink(make_client):
    c = _client(make_client)
    c.app_.state.settings.app_env = "production"
    r = _start(c, "prod@example.com")
    assert r.status_code == 503
    assert r.json()["detail"] == "email_unconfigured"


def test_email_link_to_existing_account(make_client):
    c = _client(make_client)
    first = _session(c)
    csrf = first.json()["csrf_token"]
    uid = first.json()["user"]["id"]
    email = "link-me@example.com"
    start = _start(c, email, channel="code", csrf=csrf)
    assert start.status_code == 200, start.text
    linked = c.post(
        "/api/auth/email/link",
        json={"email": email, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    )
    assert linked.status_code == 200, linked.text
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    providers = {m["provider"] for m in me.json()["methods"]}
    assert providers == {"phone", "email"}


def test_email_no_auto_merge(make_client):
    c = _client(make_client)
    email = "taken@example.com"
    start = _start(c, email, channel="code")
    v = c.post(
        "/api/auth/email/verify",
        json={"email": email, "code": start.json()["debug_code"]},
        headers=_h(),
    )
    assert v.status_code == 200
    owner = v.json()["user"]["id"]
    csrf = v.json()["csrf_token"]
    c.post("/api/auth/logout-all", headers=_h(csrf))

    other = _session(c)
    other_csrf = other.json()["csrf_token"]
    other_id = other.json()["user"]["id"]
    assert other_id != owner
    c.app_.state.auth_limiter._hits.clear()
    for ch in c.app_.state.auth_store.challenges.values():
        ch.created_at = utcnow() - timedelta(seconds=61)
        ch.consumed_at = utcnow()
    start2 = _start(c, email, channel="code", csrf=other_csrf)
    assert start2.status_code == 200, start2.text
    conflict = c.post(
        "/api/auth/email/link",
        json={"email": email, "code": start2.json()["debug_code"]},
        headers=_h(other_csrf),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "email_taken"
    me = c.get("/api/auth/me")
    assert me.json()["id"] == other_id


def test_email_verify_does_not_merge_signed_in_user(make_client):
    c = _client(make_client)
    first = _session(c)
    first_id = first.json()["user"]["id"]
    csrf = first.json()["csrf_token"]
    email = "fresh@example.com"
    start = _start(c, email, channel="code", csrf=csrf)
    v = c.post(
        "/api/auth/email/verify",
        json={"email": email, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    )
    assert v.status_code == 200
    assert v.json()["user"]["id"] != first_id
    assert v.json()["created"] is True


def test_email_unlink_method(make_client):
    c = _client(make_client)
    first = _session(c)
    csrf = first.json()["csrf_token"]
    email = "unlink@example.com"
    start = _start(c, email, channel="code", csrf=csrf)
    assert c.post(
        "/api/auth/email/link",
        json={"email": email, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    ).status_code == 200
    methods = c.get("/api/auth/methods").json()["methods"]
    email_m = next(m for m in methods if m["provider"] == "email")
    deleted = c.delete(f"/api/auth/methods/{email_m['id']}", headers=_h(csrf))
    assert deleted.status_code == 200
    left = {m["provider"] for m in c.get("/api/auth/methods").json()["methods"]}
    assert left == {"phone"}


def test_email_hash_only_when_no_encryption_key():
    from talking.auth.crypto import hash_token, load_encryption_key
    from talking.auth.mail import ConsoleMailSink
    from talking.auth.service import AuthService
    from talking.auth.storage import MemoryAuthStorage
    from talking.security import sign

    store = MemoryAuthStorage()
    svc = AuthService(
        store, secret=b"secret-key-for-tests-not-prod!!",
        log_salt=b"log-salt-for-tests", enc_key=None,
        mail_provider=ConsoleMailSink(), sign_fn=sign, allow_debug_otp=True,
    )

    async def _run():
        start = await svc.start_email(
            "hashonly@example.com", "code", origin="https://testserver",
        )
        out = await svc.verify_email("hashonly@example.com", start["debug_code"])
        ident = (await store.list_identities(out["user"]["id"]))[0]
        return ident

    ident = asyncio.run(_run())
    assert ident.email_enc is None
    assert ident.email_hash
    assert ident.email_verified is True
    assert ident.provider == "email"
    assert load_encryption_key("", None) is None
    assert ident.email_hash != hash_token("hashonly@example.com")


def test_email_encrypted_at_rest(make_client):
    c = _client(make_client)
    email = "secret@example.com"
    r = _start(c, email, channel="code")
    v = c.post(
        "/api/auth/email/verify",
        json={"email": email, "code": r.json()["debug_code"]},
        headers=_h(),
    )
    assert v.status_code == 200
    ident = next(
        i for i in c.app_.state.auth_store.identities.values() if i.provider == "email"
    )
    assert ident.email_enc
    assert email not in ident.email_enc
    assert decrypt_pii(c.app_.state.auth.enc_key, ident.email_enc) == email


def test_console_mail_does_not_log_destination_or_secret(make_client, caplog):
    c = _client(make_client)
    email = "nolog@example.com"
    with caplog.at_level(logging.INFO, logger="talking.auth"):
        r = _start(c, email)
    assert r.status_code == 200
    code = r.json()["debug_code"]
    token = r.json()["debug_token"]
    blob = " ".join(rec.getMessage() for rec in caplog.records)
    assert email not in blob
    assert "nolog" not in blob
    assert code not in blob
    assert token not in blob


def test_smtp_incomplete_does_not_fallback_to_console(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MAIL_PROVIDER", "smtp")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    from talking.auth.mail import build_mail_provider
    from talking.config import Settings
    with pytest.raises(RuntimeError, match="SMTP"):
        build_mail_provider(Settings())


def test_smtp_provider_sends_code_not_logged():
    from talking.auth.mail import SmtpMailProvider

    provider = SmtpMailProvider("smtp.example.invalid", 587, "noreply@example.invalid")
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def ehlo(self):
            return None

        def starttls(self, context=None):
            captured["starttls"] = True

        def login(self, username, password):
            captured["login"] = username

        def send_message(self, msg):
            captured["to"] = msg["To"]
            captured["subject"] = msg["Subject"]
            captured["body"] = msg.get_content()

        def quit(self):
            return None

        def close(self):
            return None

    async def _run():
        with patch("talking.auth.mail.smtplib.SMTP", FakeSMTP):
            return await provider.send(
                to_email="dest@example.com",
                subject="Talking sign-in",
                text="Code: 123456\nLink: https://example.com/api/auth/email/consume?token=abc",
                html=None,
            )

    result = asyncio.run(_run())
    assert result.ok and result.provider == "smtp"
    assert captured["to"] == "dest@example.com"
    assert "123456" in captured["body"]
    assert "token=abc" in captured["body"]
    assert captured.get("starttls") is True
