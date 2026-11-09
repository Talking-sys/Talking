"""M4 1:1 chat: nicks, DMs, receipts, typing, pagination, block/report, translation."""
from __future__ import annotations

from fastapi.testclient import TestClient

from talking.chat.nicks import NickError, validate_nick
from talking.chat.translation_cache import MessageTranslationCache
from tests.test_auth import _h, _session

ACCOUNTS = {
    "AUTH_MODE": "accounts",
    "APP_SECRET_KEY": "test-secret-key-not-for-production-use!",
    "LOG_SALT": "test-log-salt-not-a-real-salt",
    "PUBLIC_ORIGIN": "https://testserver",
    "SMS_PROVIDER": "console",
    "DB_ENCRYPTION_KEY": "test-db-encryption-key-not-secret",
    "APP_ENV": "test",
    "CHAT_NICK_RENAME_SECONDS": "0",
}


def _pair(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    a = make_client(env=env, base_url="https://testserver")
    ra = _session(a, "US")
    b = TestClient(a.app_, base_url="https://testserver")
    rb = _session(b, "GB")
    return a, ra.json(), b, rb.json()


def _set_nick(c, body, nick, **extra):
    payload = {"nickname": nick, **extra}
    r = c.patch("/api/chat/me", json=payload, headers=_h(body["csrf_token"]))
    assert r.status_code == 200, r.text
    body["csrf_token"] = r.json().get("csrf_token") or body["csrf_token"]
    return r.json()


def test_validate_nick():
    assert validate_nick("Alice") == "Alice"
    try:
        validate_nick("ab")
        assert False
    except NickError:
        pass
    try:
        validate_nick("1bad")
        assert False
    except NickError:
        pass


def test_translation_cache_per_message_and_lang():
    cache = MessageTranslationCache()
    cache.store("m1", "hello", "en", "ru", "привет", provider="stub")
    cache.store("m1", "hello", "en", "de", "hallo", provider="stub")
    assert cache.get_for("m1", "ru")["text"] == "привет"
    assert cache.get_for("m1", "de")["text"] == "hallo"
    assert cache.get("hello", "en", "ru") == "привет"
    cache.invalidate_message("m1", old_text="hello")
    assert cache.get_for("m1", "ru") is None


def test_notes_slice_status_still_exposes_notes(make_client):
    c = make_client()
    body = c.get("/api/chat").json()
    assert body["feature"] == "notes"
    assert body["core"] is True
    assert "voice_note" in body["types"]


def test_nick_unique_and_prefix_search(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice", native_lang="en")
    _set_nick(b, bb, "Alicia", native_lang="ru")
    taken = a.patch("/api/chat/me", json={"nickname": "alicia"}, headers=_h(aa["csrf_token"]))
    assert taken.status_code == 409
    hits = a.get("/api/chat/users?q=Ali").json()["users"]
    names = {u["nickname"] for u in hits}
    assert "Alicia" in names
    assert "Alice" not in names  # self excluded


def test_nick_rename_cooldown(make_client):
    a, aa, b, bb = _pair(make_client, CHAT_NICK_RENAME_SECONDS="100000")
    first = a.patch("/api/chat/me", json={"nickname": "Carol"}, headers=_h(aa["csrf_token"]))
    assert first.status_code == 200, first.text
    second = a.patch("/api/chat/me", json={"nickname": "Carole"}, headers=_h(aa["csrf_token"]))
    assert second.status_code == 429


def test_dm_by_nick_list_unread_and_last_message(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice", native_lang="en", auto_translate=True)
    _set_nick(b, bb, "Boris", native_lang="ru", auto_translate=True)
    opened = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"]))
    assert opened.status_code == 200, opened.text
    did = opened.json()["id"]
    assert opened.json()["kind"] == "direct"
    assert opened.json()["peer"]["nickname"] == "Boris"
    again = b.post("/api/chat/dialogs", json={"nickname": "Alice"}, headers=_h(bb["csrf_token"]))
    assert again.json()["id"] == did
    sent = a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "hello Boris"},
        headers=_h(aa["csrf_token"]),
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["text"] == "hello Boris"
    listed = b.get("/api/chat/dialogs").json()
    dm = next(d for d in listed if d["id"] == did)
    assert dm["unread_count"] == 1
    assert "hello Boris" in (dm["last_message"] or {}).get("text", "")
    b_msgs = b.get(f"/api/chat/dialogs/{did}/messages").json()
    assert b_msgs[-1]["text"] == "hello Boris"
    assert b_msgs[-1]["show_original"] is True
    assert b_msgs[-1]["translation"]
    assert b_msgs[-1]["original"] == "hello Boris"


def test_invite_link_starts_dm(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice")
    inv = a.post("/api/chat/invites", headers=_h(aa["csrf_token"]))
    assert inv.status_code == 200, inv.text
    token = inv.json()["token"]
    peek = b.get("/api/chat/invites/" + token)
    assert peek.status_code == 200
    assert peek.json()["from"]["nickname"] == "Alice"
    acc = b.post("/api/chat/invites/" + token + "/accept", headers=_h(bb["csrf_token"]))
    assert acc.status_code == 200, acc.text
    assert acc.json()["kind"] == "direct"
    again = b.post("/api/chat/invites/" + token + "/accept", headers=_h(bb["csrf_token"]))
    assert again.status_code in (410, 404)


def test_block_and_report_no_message_contents(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice")
    _set_nick(b, bb, "Boris")
    d = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()
    did = d["id"]
    a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "secret-body-should-not-be-reported"},
        headers=_h(aa["csrf_token"]),
    )
    blocked = a.post("/api/chat/blocks", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"]))
    assert blocked.status_code == 200, blocked.text
    denied = b.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "hi"},
        headers=_h(bb["csrf_token"]),
    )
    assert denied.status_code == 403
    hidden = a.get("/api/chat/users?q=Bor").json()["users"]
    assert hidden == []
    rep = a.post(
        "/api/chat/reports",
        json={"nickname": "Boris", "reason": "spam", "text": "secret-body-should-not-be-reported"},
        headers=_h(aa["csrf_token"]),
    )
    assert rep.status_code == 200, rep.text
    dump = a.app_.state.chat_directory.list_reports_admin()
    blob = str(dump)
    assert "secret-body-should-not-be-reported" not in blob
    assert "Boris" not in blob
    assert dump[-1]["reason"] == "spam"
    assert dump[-1]["reporter_hash"]
    a.delete("/api/chat/blocks/" + bb["user"]["id"], headers=_h(aa["csrf_token"]))
    ok = b.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "back"},
        headers=_h(bb["csrf_token"]),
    )
    assert ok.status_code == 201, ok.text


def test_pagination_and_sync(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice")
    _set_nick(b, bb, "Boris")
    did = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()["id"]
    ids = []
    for i in range(5):
        r = a.post(
            f"/api/chat/dialogs/{did}/messages",
            json={"type": "text", "text": f"m{i}"},
            headers=_h(aa["csrf_token"]),
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    page = a.get(f"/api/chat/dialogs/{did}/messages?limit=2").json()
    assert [m["text"] for m in page] == ["m3", "m4"]
    older = a.get(f"/api/chat/dialogs/{did}/messages?limit=2&before={page[0]['id']}").json()
    assert [m["text"] for m in older] == ["m1", "m2"]
    assert a.get(f"/api/chat/dialogs/{did}/messages?limit=2").headers["x-chat-has-more"] == "1"
    mid_ts = a.get(f"/api/chat/dialogs/{did}/messages").json()[2]["created_at"]
    synced = a.get(f"/api/chat/sync?after={mid_ts}").json()
    assert synced["ok"] is True
    assert all(m["created_at"] > mid_ts for m in synced["messages"])


def test_delete_notifies_peer_and_sync_tombstone(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice")
    _set_nick(b, bb, "Boris")
    did = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()["id"]
    msg = a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "gone"},
        headers=_h(aa["csrf_token"]),
    ).json()
    tok = b.get("/api/ws-token").json()["token"]
    with b.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
        # connect may deliver receipts; drain until deleted
        gone = a.delete("/api/chat/messages/" + msg["id"], headers=_h(aa["csrf_token"]))
        assert gone.status_code == 200
        ev = None
        for _ in range(8):
            item = ws.receive_json()
            if item.get("type") == "chat.deleted":
                ev = item
                break
        assert ev is not None
        assert ev["message_id"] == msg["id"]
    tomb = b.get("/api/chat/sync?after=0").json()["deleted"]
    assert any(t["id"] == msg["id"] for t in tomb)


def test_ws_typing_receipts_and_resync(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice", native_lang="en")
    _set_nick(b, bb, "Boris", native_lang="ru")
    did = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()["id"]
    ta = a.get("/api/ws-token").json()["token"]
    tb = b.get("/api/ws-token").json()["token"]
    with a.websocket_connect("/ws/chat?t=" + ta) as wsa, b.websocket_connect("/ws/chat?t=" + tb) as wsb:
        assert wsa.receive_json()["type"] == "chat.hello"
        assert wsb.receive_json()["type"] == "chat.hello"
        wsa.send_json({"type": "chat.typing", "dialog_id": did, "on": True})
        typing = None
        for _ in range(8):
            item = wsb.receive_json()
            if item.get("type") == "chat.typing":
                typing = item
                break
        assert typing is not None
        assert typing["on"] is True
        sent = a.post(
            f"/api/chat/dialogs/{did}/messages",
            json={"type": "text", "text": "ping"},
            headers=_h(aa["csrf_token"]),
        )
        assert sent.status_code == 201, sent.text
        ev = None
        for _ in range(8):
            item = wsb.receive_json()
            if item.get("type") == "chat.message":
                ev = item
                break
        assert ev is not None
        assert ev["message"]["text"] == "ping"
        rec = None
        for _ in range(8):
            item = wsa.receive_json()
            if item.get("type") == "chat.receipt":
                rec = item
                break
        assert rec is not None
        assert rec["status"] in ("delivered", "read")
        b.post(
            f"/api/chat/dialogs/{did}/read",
            json={"last_id": sent.json()["id"]},
            headers=_h(bb["csrf_token"]),
        )
        read_ev = None
        for _ in range(8):
            item = wsa.receive_json()
            if item.get("type") == "chat.receipt" and item.get("status") == "read":
                read_ev = item
                break
        assert read_ev is not None
        wsb.send_json({"type": "chat.sync", "after": 0})
        sync = None
        for _ in range(8):
            item = wsb.receive_json()
            if item.get("type") == "chat.sync":
                sync = item
                break
        assert sync is not None
        assert any(m["text"] == "ping" for m in sync["messages"])


def test_command_not_translated_and_failure_mark(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice", native_lang="en")
    _set_nick(b, bb, "Boris", native_lang="de")
    did = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()["id"]
    cmd = a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "translate to German"},
        headers=_h(aa["csrf_token"]),
    )
    assert cmd.status_code == 201, cmd.text
    peer_view = b.get(f"/api/chat/dialogs/{did}/messages").json()[-1]
    assert peer_view["command"] is True
    assert peer_view["translation"] in (None, "")
    assert peer_view["text"] == "translate to German"

    class Boom:
        def translate(self, *a, **k):
            raise RuntimeError("mt down")

    a.app_.state.engine.translator = Boom()
    msg = a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "still there"},
        headers=_h(aa["csrf_token"]),
    )
    assert msg.status_code == 201
    failed = b.get(f"/api/chat/dialogs/{did}/messages").json()[-1]
    assert failed["translation_failed"] is True
    assert failed["text"] == "still there"


def test_per_dialog_lang_override_uses_cache(make_client):
    a, aa, b, bb = _pair(make_client)
    _set_nick(a, aa, "Alice", native_lang="en")
    _set_nick(b, bb, "Boris", native_lang="ru")
    did = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()["id"]
    a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "hello"},
        headers=_h(aa["csrf_token"]),
    )
    ru = b.get(f"/api/chat/dialogs/{did}/messages").json()[-1]
    assert ru["tgt_lang"] == "ru"
    assert ru["translation"]
    b.patch(
        f"/api/chat/dialogs/{did}",
        json={"target_lang": "de"},
        headers=_h(bb["csrf_token"]),
    )
    de = b.get(f"/api/chat/dialogs/{did}/messages").json()[-1]
    assert de["tgt_lang"] == "de"
    assert de["translation"]
    cache = a.app_.state.chat_translations
    assert cache.get_for(ru["id"], "ru")
    assert cache.get_for(ru["id"], "de")
    off = b.patch("/api/chat/me", json={"auto_translate": False}, headers=_h(bb["csrf_token"]))
    assert off.status_code == 200
    b.patch(f"/api/chat/dialogs/{did}", json={"auto_translate": False}, headers=_h(bb["csrf_token"]))
    raw = b.get(f"/api/chat/dialogs/{did}/messages").json()[-1]
    assert raw["translation"] in (None, "")
    assert raw["show_original"] is False


def test_non_participant_cannot_read_dm(make_client):
    a, aa, b, bb = _pair(make_client)
    c = TestClient(a.app_, base_url="https://testserver")
    rc = _session(c, "FR")
    _set_nick(a, aa, "Alice")
    _set_nick(b, bb, "Boris")
    did = a.post("/api/chat/dialogs", json={"nickname": "Boris"}, headers=_h(aa["csrf_token"])).json()["id"]
    a.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "private"},
        headers=_h(aa["csrf_token"]),
    )
    r = c.get(f"/api/chat/dialogs/{did}/messages")
    assert r.status_code == 404
    post = c.post(
        f"/api/chat/dialogs/{did}/messages",
        json={"type": "text", "text": "nope"},
        headers=_h(rc.json()["csrf_token"]),
    )
    assert post.status_code in (403, 404)


def test_admin_redacts_text_as_well_as_transcript():
    from talking.chat.models import Message
    msg = Message(
        id="m1", dialog_id="dmabc", sender_id="u1", type="text",
        created_at=1.0, text="secret chat line", translation="секрет",
    )
    redacted = msg.to_admin()
    assert "secret chat line" not in str(redacted)
    assert "секрет" not in str(redacted)
    assert redacted["content_redacted"] is True
