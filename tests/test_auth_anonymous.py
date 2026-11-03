"""Anonymous accounts as a product: limits, upgrade, recovery, expiry, anti-abuse."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from talking.auth.capabilities import (
    ALL_CAPABILITIES,
    DEFAULT_ANON_CAPABILITIES,
    parse_capability_set,
    parse_recovery_reissue,
    should_reissue_recovery,
)
from talking.auth.crypto import utcnow
from talking.auth.jobs import purge_abandoned_anonymous
from talking.auth.userdata import cleaner_from_app

from tests.test_auth import ACCOUNTS, _anon, _h
from tests.test_auth_phone import _phone, _start


def _client(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def test_capability_parsers():
    assert parse_capability_set("", default=DEFAULT_ANON_CAPABILITIES) == DEFAULT_ANON_CAPABILITIES
    assert parse_capability_set("none") == frozenset()
    assert parse_capability_set("all") == ALL_CAPABILITIES
    assert "chat" in parse_capability_set("chat,not-a-cap")
    assert parse_recovery_reissue("off") == "never"
    assert parse_recovery_reissue("if_anonymous") == "if_anonymous"
    assert should_reissue_recovery("always", False) is True
    assert should_reissue_recovery("never", True) is False
    assert should_reissue_recovery("if_anonymous", True) is True
    assert should_reissue_recovery("if_anonymous", False) is False


def test_anonymous_policy_and_me_capabilities(make_client):
    c = _client(make_client)
    policy = c.get("/api/auth/anonymous/policy")
    assert policy.status_code == 200
    body = policy.json()
    assert "chat" in body["policy"]["allowed"]
    assert "transcription" in body["policy"]["denied"]
    assert body["policy"]["limits"]["create_per_ip"] == 5
    r = _anon(c)
    assert r.json().get("shown_once") is True
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    data = me.json()
    assert data["anonymous"] is True
    assert data["capabilities"]["anonymous"] is True
    assert "chat" in data["capabilities"]["allowed"]
    assert "games" in data["capabilities"]["denied"]
    assert data["idle_expires_at"]
    chat = c.get("/api/chat")
    assert chat.status_code == 200
    assert chat.json()["anonymous"] is True
    assert chat.json()["limits"]["messages_per_min"] == 5
    assert chat.json()["limits"]["quota_bytes"] == 50 * 1024 * 1024


def test_anonymous_capability_denials(make_client):
    c = _client(make_client)
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    games = c.get("/api/games")
    assert games.status_code == 403
    assert games.json()["detail"]["error"] == "capability_denied"
    assert games.json()["detail"]["capability"] == "games"
    coins = c.get("/api/commercial/talk-coins")
    assert coins.status_code == 403
    checkout = c.post("/api/commercial/checkout", json={"plan": "premium"}, headers=_h(csrf))
    assert checkout.status_code == 403
    transcribe = c.post("/api/chat/messages/mdeadbeefdeadbee/transcribe", headers=_h(csrf))
    assert transcribe.status_code == 403
    assert transcribe.json()["detail"]["capability"] == "transcription"


def test_anonymous_denied_capability_override(make_client):
    c = _client(make_client, ANON_DENIED_CAPABILITIES="chat")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    denied = c.post(
        "/api/chat/dialogs/local/messages",
        json={"type": "text", "text": "hi"},
        headers=_h(csrf),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["capability"] == "chat"


def test_local_mode_ignores_anonymous_limits(make_client):
    c = make_client()
    assert c.get("/api/games").status_code == 501
    coins = c.get("/api/commercial/talk-coins")
    assert coins.status_code == 200
    me = c.get("/api/auth/me")
    assert me.json()["auth_mode"] == "local"
    assert me.json()["capabilities"]["anonymous"] is False


def test_anonymous_chat_keeps_data_on_phone_link(make_client):
    c = _client(make_client)
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    sent = c.post(
        "/api/chat/dialogs/local/messages",
        json={"type": "text", "text": "keep me"},
        headers=_h(csrf),
    )
    assert sent.status_code == 201, sent.text
    phone = _phone()
    start = _start(c, phone, csrf=csrf)
    linked = c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["upgraded"] is True
    assert linked.json()["kept_user_id"] is True
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    assert me.json()["anonymous"] is False
    msgs = c.get("/api/chat/dialogs/local/messages")
    texts = [m.get("text") for m in msgs.json()]
    assert "keep me" in texts


def test_phone_verify_while_signed_in_upgrades_in_place(make_client):
    c = _client(make_client)
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    phone = _phone("GB")
    start = _start(c, phone, csrf=csrf)
    verified = c.post(
        "/api/auth/phone/verify",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["upgraded"] is True
    me = c.get("/api/auth/me")
    assert me.json()["id"] == uid
    assert me.json()["anonymous"] is False
    providers = {m["provider"] for m in me.json()["methods"]}
    assert providers == {"anonymous", "phone"}


def test_identity_taken_requires_explicit_merge_confirmation(make_client):
    c = _client(make_client)
    phone = _phone("DE")
    start = _start(c, phone)
    owned = c.post(
        "/api/auth/phone/verify",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(),
    )
    assert owned.status_code == 200
    owner = owned.json()["user"]["id"]
    owner_csrf = owned.json()["csrf_token"]
    c.post("/api/auth/logout-all", headers=_h(owner_csrf))

    other = _anon(c)
    other_csrf = other.json()["csrf_token"]
    other_id = other.json()["user"]["id"]
    saved = c.post(
        "/api/chat/dialogs/local/messages",
        json={"type": "text", "text": "anon note"},
        headers=_h(other_csrf),
    )
    assert saved.status_code == 201, saved.text
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
    body = conflict.json()
    assert body["detail"] == "phone_taken"
    assert body["merge_required"] is True
    token = body["merge_token"]
    assert token
    me = c.get("/api/auth/me")
    assert me.json()["id"] == other_id

    missing = c.post(
        "/api/auth/upgrade/confirm",
        json={"confirm": False, "merge_token": token},
        headers=_h(other_csrf),
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "confirm_required"

    merged = c.post(
        "/api/auth/upgrade/confirm",
        json={"confirm": True, "merge_token": token},
        headers=_h(other_csrf),
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged"] is True
    assert merged.json()["user"]["id"] == owner
    me2 = c.get("/api/auth/me")
    assert me2.json()["id"] == owner
    texts = [m.get("text") for m in c.get("/api/chat/dialogs/local/messages").json()]
    assert "anon note" in texts
    gone = c.app_.state.auth_store.users[other_id]
    assert gone.status == "deleted"


def test_recovery_reissue_never(make_client):
    c = _client(make_client, ANON_RECOVERY_REISSUE="never")
    r = _anon(c)
    code = r.json()["recovery_code"]
    csrf = r.json()["csrf_token"]
    c.post("/api/auth/logout-all", headers=_h(csrf))
    rec = c.post("/api/auth/recover", json={"code": code}, headers=_h())
    assert rec.status_code == 200, rec.text
    assert rec.json()["recovery_reissued"] is False
    assert "recovery_code" not in rec.json()
    again = c.post("/api/auth/recover", json={"code": code}, headers=_h(rec.json()["csrf_token"]))
    assert again.status_code == 401


def test_recovery_reissue_if_anonymous_skips_after_upgrade(make_client):
    c = _client(make_client, ANON_RECOVERY_REISSUE="if_anonymous")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    phone = _phone("FR")
    start = _start(c, phone, csrf=csrf)
    assert c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    ).status_code == 200
    rotated = c.post("/api/auth/recovery/rotate", headers=_h(csrf))
    assert rotated.status_code == 200
    code = rotated.json()["recovery_code"]
    c.post("/api/auth/logout-all", headers=_h(csrf))
    rec = c.post("/api/auth/recover", json={"code": code}, headers=_h())
    assert rec.status_code == 200
    assert rec.json()["recovery_reissued"] is False


def test_recovery_rotate_invalidates_previous(make_client):
    c = _client(make_client)
    r = _anon(c)
    old = r.json()["recovery_code"]
    csrf = r.json()["csrf_token"]
    rotated = c.post("/api/auth/recovery/rotate", headers=_h(csrf))
    assert rotated.status_code == 200
    new = rotated.json()["recovery_code"]
    assert new != old
    c.post("/api/auth/logout-all", headers=_h(csrf))
    assert c.post("/api/auth/recover", json={"code": old}, headers=_h()).status_code == 401
    ok = c.post("/api/auth/recover", json={"code": new}, headers=_h())
    assert ok.status_code == 200


def test_anonymous_create_already_signed_in(make_client):
    c = _client(make_client)
    r = _anon(c)
    again = c.post("/api/auth/anonymous", headers=_h(r.json()["csrf_token"]))
    assert again.status_code == 400
    assert again.json()["detail"] == "already_signed_in"


def test_anonymous_chat_message_rate_limit(make_client):
    c = _client(make_client, ANON_CHAT_MSG_PER_MIN="1")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    first = c.post(
        "/api/chat/dialogs/local/messages",
        json={"type": "text", "text": "one"},
        headers=_h(csrf),
    )
    assert first.status_code == 201, first.text
    second = c.post(
        "/api/chat/dialogs/local/messages",
        json={"type": "text", "text": "two"},
        headers=_h(csrf),
    )
    assert second.status_code == 429


def test_anonymous_create_ip_limit_clean_client(make_client):
    c = _client(make_client, ANON_CREATE_PER_IP="2", ANON_CREATE_PER_IP_DAY="10")

    def mint_and_logout():
        r = c.post("/api/auth/anonymous", headers=_h())
        if r.status_code == 201:
            c.post("/api/auth/logout-all", headers=_h(r.json()["csrf_token"]))
        return r

    assert mint_and_logout().status_code == 201
    assert mint_and_logout().status_code == 201
    third = c.post("/api/auth/anonymous", headers=_h())
    assert third.status_code == 429


def test_abandoned_anonymous_purged_with_files(make_client):
    c = _client(make_client, ANON_IDLE_SECONDS="3600")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    sent = c.post(
        "/api/chat/dialogs/local/messages",
        json={"type": "text", "text": "ephemeral"},
        headers=_h(csrf),
    )
    assert sent.status_code == 201, sent.text
    user = c.app_.state.auth_store.users[uid]
    user.last_seen_at = utcnow() - timedelta(days=40)
    out = asyncio.run(purge_abandoned_anonymous(
        c.app_.state.auth, cleaner=cleaner_from_app(c.app_),
    ))
    assert out["purged"] == 1
    assert c.app_.state.auth_store.users[uid].status == "deleted"
    assert not (Path(c.app_.state.chat_store.root) / uid / "dialogs.json").exists()
    assert c.get("/api/auth/me").status_code == 401


def test_cleanup_spares_upgraded_and_fresh_accounts(make_client):
    c = _client(make_client, ANON_IDLE_SECONDS="60")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    phone = _phone("CA")
    start = _start(c, phone, csrf=csrf)
    assert c.post(
        "/api/auth/phone/link",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(csrf),
    ).status_code == 200
    upgraded = c.get("/api/auth/me").json()["id"]
    c.post("/api/auth/logout-all", headers=_h(csrf))
    fresh = c.post("/api/auth/anonymous", headers=_h())
    assert fresh.status_code == 201, fresh.text
    fresh_id = fresh.json()["user"]["id"]
    store = c.app_.state.auth_store
    store.users[upgraded].last_seen_at = utcnow() - timedelta(days=40)
    out = asyncio.run(purge_abandoned_anonymous(
        c.app_.state.auth, cleaner=cleaner_from_app(c.app_),
    ))
    assert out["purged"] == 0
    assert store.users[fresh_id].status == "active"
    assert store.users[upgraded].status == "active"


def test_idle_disabled_skips_purge(make_client):
    c = _client(make_client, ANON_IDLE_SECONDS="0")
    r = _anon(c)
    uid = r.json()["user"]["id"]
    c.app_.state.auth_store.users[uid].last_seen_at = utcnow() - timedelta(days=400)
    out = asyncio.run(purge_abandoned_anonymous(c.app_.state.auth))
    assert out["purged"] == 0
    assert out["skipped"] == "disabled"
    assert c.app_.state.auth_store.users[uid].status == "active"
