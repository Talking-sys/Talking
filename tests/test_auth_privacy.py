"""M10 stage 7: account deletion, export, user-facing audit trail."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from talking.auth.crypto import utcnow
from talking.chat.models import Message, redact_for_admin

from tests.test_auth import ACCOUNTS, _anon, _h


def _client(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def test_local_mode_privacy_is_accounts_only(make_client):
    c = make_client()
    assert c.get("/account").status_code == 200
    assert "export" in c.get("/account").text.lower() or "экспорт" in c.get("/account").text.lower()
    assert c.get("/api/auth/events").json()["events"] == []
    assert c.post("/api/auth/export").status_code == 400
    assert c.post("/api/auth/account/delete", json={"confirm": "DELETE"}).status_code == 400


def test_audit_events_hide_secrets_and_list_sign_ins(make_client):
    c = _client(make_client)
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    recovery = r.json()["recovery_code"]
    listed = c.get("/api/auth/sessions").json()["sessions"]
    sid = listed[0]["id"]
    c.delete(f"/api/auth/sessions/{sid}", headers=_h(csrf))
    rec = c.post("/api/auth/recover", json={"code": recovery}, headers=_h())
    assert rec.status_code == 200
    events = c.get("/api/auth/events").json()["events"]
    names = [e["event"] for e in events]
    assert "anonymous_create" in names
    assert "session_revoke" in names
    assert "recover" in names
    blob = str(events)
    assert recovery not in blob
    assert "ip_hash" not in blob
    assert "refresh" not in blob.lower() or "refresh_reuse" in names
    for ev in events:
        assert "ip_hash" not in ev
        assert "detail" in ev
        assert set(ev["detail"]).issubset({"channel", "created", "provider", "family", "already", "linked", "grace_s"})


def test_delete_requires_confirm_and_fresh_session(make_client):
    c = _client(make_client, AUTH_DELETE_GRACE_S="3600")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    missing = c.post("/api/auth/account/delete", json={}, headers=_h(csrf))
    assert missing.status_code == 400
    assert missing.json()["detail"] == "confirm_required"
    sess = r.json()["user"]  # session lives on store
    store = c.app_.state.auth_store
    row = next(iter(store.sessions.values()))
    row.auth_at = utcnow() - timedelta(minutes=20)
    stale = c.post("/api/auth/account/delete", json={"confirm": "DELETE"}, headers=_h(csrf))
    assert stale.status_code == 403
    assert stale.json()["detail"] == "fresh_session_required"


def test_delete_grace_is_cancellable(make_client):
    c = _client(make_client, AUTH_DELETE_GRACE_S="3600")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    c.post("/api/chat/messages", json={"text": "keep me"}, headers=_h(csrf))
    out = c.post("/api/auth/account/delete", json={"confirm": "DELETE"}, headers=_h(csrf))
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "deleting"
    assert out.json()["can_cancel"] is True
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["status"] == "deleting"
    cancel = c.post("/api/auth/account/delete/cancel", headers=_h(csrf), json={})
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "active"
    assert c.get("/api/auth/me").json()["status"] == "active"
    msgs = c.get("/api/chat/messages").json()["messages"]
    assert any(m["text"] == "keep me" for m in msgs)
    assert c.app_.state.auth_store.users[uid].status == "active"


def test_hard_delete_wipes_stores_and_closes_sockets(make_client):
    c = _client(make_client, AUTH_DELETE_GRACE_S="0")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    c.post("/api/chat/messages", json={"text": "wipe me please"}, headers=_h(csrf))
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
        gone = c.post("/api/auth/account/delete", json={"confirm": "DELETE"}, headers=_h(csrf))
        assert gone.status_code == 200, gone.text
        assert gone.json()["status"] == "deleted"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert c.get("/api/auth/me").status_code == 401
    user = c.app_.state.auth_store.users[uid]
    assert user.status == "deleted"
    assert user.nickname is None
    assert [i for i in c.app_.state.auth_store.identities.values() if i.user_id == uid] == []
    assert [s for s in c.app_.state.auth_store.sessions.values() if s.user_id == uid] == []
    chat_root = Path(c.app_.state.settings.data_dir) / "chat" / uid
    assert not chat_root.exists()
    dump = c.app_.state.chat_store.dump_user(uid)
    assert dump == []


def test_process_due_deletions_after_grace(make_client):
    c = _client(make_client, AUTH_DELETE_GRACE_S="3600")
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    c.post("/api/auth/account/delete", json={"confirm": "DELETE"}, headers=_h(csrf))
    user = c.app_.state.auth_store.users[uid]
    user.delete_after = utcnow() - timedelta(seconds=1)
    n = asyncio.run(c.app_.state.privacy.process_due_deletions())
    assert n == 1
    assert c.app_.state.auth_store.users[uid].status == "deleted"


def test_export_json_rate_limit_and_short_lived_download(make_client):
    c = _client(make_client)
    r = _anon(c)
    csrf = r.json()["csrf_token"]
    uid = r.json()["user"]["id"]
    c.post("/api/chat/messages", json={"text": "exported line"}, headers=_h(csrf))
    job = c.post("/api/auth/export", headers=_h(csrf), json={})
    assert job.status_code == 202, job.text
    eid = job.json()["id"]
    st = c.get(f"/api/auth/export/{eid}")
    assert st.status_code == 200
    assert st.json()["status"] == "ready"
    token = st.json()["download_token"]
    assert token
    denied = c.get(f"/api/auth/export/{eid}/download")
    assert denied.status_code == 403
    dl = c.get(f"/api/auth/export/{eid}/download?token={token}")
    assert dl.status_code == 200
    assert "attachment" in dl.headers.get("content-disposition", "")
    data = dl.json()
    assert data["user"]["id"] == uid
    assert any(m.get("text") == "exported line" for d in data["chat"] for m in d.get("messages", []))
    assert "security_events" in data
    blob = str(data)
    assert "refresh_hash" not in blob
    assert "phone_enc" not in blob
    assert "subject" not in blob or all("subject" not in m for m in data["methods"])
    other = _client(make_client)
    other_anon = _anon(other)
    steal = other.get(f"/api/auth/export/{eid}/download?token={token}", headers=_h(other_anon.json()["csrf_token"]))
    assert steal.status_code in (403, 404)
    for _ in range(3):
        extra = c.post("/api/auth/export", headers=_h(csrf), json={})
        if extra.status_code == 429:
            break
    else:
        fourth = c.post("/api/auth/export", headers=_h(csrf), json={})
        assert fourth.status_code == 429


def test_admin_redaction_strips_message_text():
    msg = Message(
        id="m1", dialog_id="local", sender_id="local", type="text",
        created_at=1.0, text="secret chat", transcript="secret speech",
        translation="секрет", original_text="secret original",
    )
    redacted = msg.to_admin()
    dumped = str(redacted)
    assert "secret" not in dumped
    assert "секрет" not in dumped
    assert "text" not in redacted
    assert "transcript" not in redacted
    assert redacted["content_redacted"] is True
    assert redacted["transcript_redacted"] is True
    public = {"text": "secret chat", "transcript": "secret speech", "id": "m1"}
    assert "secret" not in str(redact_for_admin(public))


def test_events_purge_retention(make_client):
    c = _client(make_client, AUTH_EVENTS_RETENTION_DAYS="1")
    _anon(c)
    store = c.app_.state.auth_store
    old = store.events[0]
    old.created_at = utcnow() - timedelta(days=2)
    n = asyncio.run(c.app_.state.privacy.purge_old_events())
    assert n >= 1
    assert old not in store.events


def test_privacy_page_mentions_deletion(make_client):
    html = make_client().get("/privacy").text
    assert "deletion" in html.lower() or "удален" in html.lower()
    assert "/account" in html
