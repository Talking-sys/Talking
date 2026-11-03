"""Group conversations: membership, invites, per-recipient translation, WS fan-out."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from talking.chat.groups import GroupError, GroupStore
from talking.chat.models import Message, redact_for_admin
from talking.translate import StubProvider, Translator

from tests.test_auth import ACCOUNTS, _anon, _h


def _accounts(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def _users(make_client, n=2, **extra):
    root = _accounts(make_client, **extra)
    out = []
    for _ in range(n):
        c = TestClient(root.app_, base_url="https://testserver")
        c.eng = root.eng
        c.app_ = root.app_
        r = _anon(c)
        out.append((c, r.json()["csrf_token"], r.json()["user"]["id"]))
    return out


def _user(make_client, **extra):
    return _users(make_client, n=1, **extra)[0]


def test_group_store_create_roles_leave_and_limit(tmp_path):
    store = GroupStore(tmp_path)
    g = store.create("alice", "Trip", lang="en", member_ids=["bob"], max_members=3)
    assert g.member("alice").role == "owner"
    assert g.member("bob").role == "member"
    store.add_member(g.id, "alice", "cara", lang="de", max_members=3)
    with pytest.raises(GroupError) as e:
        store.add_member(g.id, "alice", "dan", max_members=3)
    assert e.value.status == 409
    remaining = store.leave(g.id, "alice")
    assert remaining is not None
    assert remaining.member("bob").role == "owner"
    assert remaining.member("alice") is None


def test_invite_expiry_and_max_uses(tmp_path):
    store = GroupStore(tmp_path)
    g = store.create("alice", "Club", lang="en")
    _group, inv, token = store.create_invite(g.id, "alice", ttl_s=60, max_uses=1)
    assert inv.to_public().get("token") is None
    joined = store.join(token, "bob", lang="ru")
    assert joined.is_member("bob")
    with pytest.raises(GroupError) as e:
        store.join(token, "cara")
    assert e.value.status in (404, 410)
    _g2, _inv2, token2 = store.create_invite(g.id, "alice", ttl_s=1, max_uses=10)
    time.sleep(1.05)
    with pytest.raises(GroupError) as e:
        store.join(token2, "cara")
    assert e.value.status == 410


def test_create_group_and_list_in_dialogs(make_client):
    c = make_client()
    r = c.post("/api/chat/groups", json={"name": "Команда", "avatar": "🚀", "description": "hi"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "group"
    assert body["title"] == "Команда"
    assert body["avatar"] == "🚀"
    assert body["role"] == "owner"
    assert body["member_count"] == 1
    dialogs = c.get("/api/chat/dialogs").json()
    assert dialogs[0]["id"] == "local"
    assert any(d["id"] == body["id"] and d["kind"] == "group" for d in dialogs)
    listed = c.get("/api/chat/groups").json()
    assert listed["max_members"] == 20
    assert listed["groups"][0]["id"] == body["id"]


def test_member_limit_configurable(make_client):
    c = make_client(env={"CHAT_GROUP_MAX_MEMBERS": "2"})
    g = c.post("/api/chat/groups", json={"name": "Duo"}).json()
    add = c.post(f"/api/chat/groups/{g['id']}/members", json={"user_id": "bob"})
    assert add.status_code == 200, add.text
    blocked = c.post(f"/api/chat/groups/{g['id']}/members", json={"user_id": "cara"})
    assert blocked.status_code == 409


def test_commercial_trial_cap_fail_closed(make_client):
    c = make_client(env={
        "CHAT_GROUP_MAX_MEMBERS": "20",
        "CHAT_GROUP_TRIAL_MAX_MEMBERS": "2",
        "COMMERCIAL_API_URL": "http://127.0.0.1:1",
        "COMMERCIAL_ENTITLEMENT_CACHE_TTL": "0",
    })
    g = c.post("/api/chat/groups", json={"name": "Trial"}).json()
    assert c.post(f"/api/chat/groups/{g['id']}/members", json={"user_id": "bob"}).status_code == 200
    assert c.post(f"/api/chat/groups/{g['id']}/members", json={"user_id": "cara"}).status_code == 409


def test_non_member_cannot_read_or_post(make_client):
    (owner, o_csrf, _oid), (outsider, x_csrf, _xid) = _users(make_client, 2)
    g = owner.post("/api/chat/groups", json={"name": "Secret"}, headers=_h(o_csrf)).json()
    gid = g["id"]
    denied = outsider.get(f"/api/chat/groups/{gid}")
    assert denied.status_code == 403
    listed = outsider.get(f"/api/chat/dialogs/{gid}/messages")
    assert listed.status_code in (403, 404)
    posted = outsider.post(
        f"/api/chat/dialogs/{gid}/messages",
        json={"type": "text", "text": "poke"},
        headers=_h(x_csrf),
    )
    assert posted.status_code in (403, 404)
    via_messages = outsider.get(f"/api/chat/messages?conversation_id={gid}")
    assert via_messages.status_code == 403


def test_roles_add_remove_and_leave(make_client):
    (owner, o_csrf, oid), (member, m_csrf, mid), (stranger, s_csrf, sid) = _users(make_client, 3)
    g = owner.post("/api/chat/groups", json={"name": "Crew"}, headers=_h(o_csrf)).json()
    gid = g["id"]
    added = owner.post(
        f"/api/chat/groups/{gid}/members",
        json={"user_id": mid, "role": "admin", "lang": "de"},
        headers=_h(o_csrf),
    )
    assert added.status_code == 200, added.text
    roles = {m["user_id"]: m["role"] for m in added.json()["members"]}
    assert roles[oid] == "owner"
    assert roles[mid] == "admin"
    kicked_denied = member.delete(
        f"/api/chat/groups/{gid}/members/{oid}", headers=_h(m_csrf),
    )
    assert kicked_denied.status_code == 403
    owner.post(
        f"/api/chat/groups/{gid}/members",
        json={"user_id": sid},
        headers=_h(o_csrf),
    )
    kicked = member.delete(
        f"/api/chat/groups/{gid}/members/{sid}", headers=_h(m_csrf),
    )
    assert kicked.status_code == 200, kicked.text
    left = stranger.post(f"/api/chat/groups/{gid}/leave", headers=_h(s_csrf))
    assert left.status_code == 403  # already removed
    member.post(f"/api/chat/groups/{gid}/leave", headers=_h(m_csrf))
    gone = member.get(f"/api/chat/groups/{gid}")
    assert gone.status_code == 403


def test_invite_link_join(make_client):
    (owner, o_csrf, _oid), (guest, g_csrf, gid_user) = _users(make_client, 2)
    group = owner.post("/api/chat/groups", json={"name": "Open"}, headers=_h(o_csrf)).json()
    inv = owner.post(
        f"/api/chat/groups/{group['id']}/invites",
        json={"expires_in": 3600, "max_uses": 2},
        headers=_h(o_csrf),
    )
    assert inv.status_code == 201, inv.text
    token = inv.json()["token"]
    assert "token_hash" not in inv.json()
    preview = guest.get(f"/api/chat/groups/join/{token}")
    assert preview.status_code == 200
    assert "members" not in preview.json()["group"]
    joined = guest.post("/api/chat/groups/join", json={"token": token, "lang": "es"}, headers=_h(g_csrf))
    assert joined.status_code == 200, joined.text
    assert any(m["user_id"] == gid_user for m in joined.json()["members"])
    listed = owner.get(f"/api/chat/groups/{group['id']}/invites")
    assert listed.status_code == 200
    assert "token" not in listed.json()["invites"][0]


def test_per_recipient_translation_and_cache(make_client):
    (owner, o_csrf, oid), (peer, p_csrf, pid) = _users(make_client, 2)
    g = owner.post(
        "/api/chat/groups",
        json={"name": "Langs", "lang": "en", "member_ids": [pid]},
        headers=_h(o_csrf),
    )
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    owner.patch(
        f"/api/chat/groups/{gid}/members/{oid}",
        json={"lang": "en"},
        headers=_h(o_csrf),
    )
    peer.patch(
        f"/api/chat/groups/{gid}/members/{pid}",
        json={"lang": "ru"},
        headers=_h(p_csrf),
    )
    posted = owner.post(
        f"/api/chat/dialogs/{gid}/messages",
        json={"type": "text", "text": "hello group", "src_lang": "en"},
        headers=_h(o_csrf),
    )
    assert posted.status_code == 201, posted.text
    mine = posted.json()
    assert mine["translation"].startswith("[en→en]")
    assert mine["translation_status"] == "ok"
    theirs = peer.get(f"/api/chat/dialogs/{gid}/messages").json()[-1]
    assert theirs["translation"].startswith("[en→ru]")
    assert theirs["text"] == "hello group"
    assert theirs["original"] == "hello group"
    trans = owner.app_.state.chat_translations
    assert trans.get("hello group", "en", "ru") == theirs["translation"]
    again = owner.post(
        f"/api/chat/dialogs/{gid}/messages",
        json={"type": "text", "text": "hello group", "src_lang": "en"},
        headers=_h(o_csrf),
    )
    assert again.json()["translation_provider"] in ("cache", "stub")


def test_translation_fallback_and_command_filter(make_client):
    class Boom(StubProvider):
        def translate(self, text, src, tgt):
            raise RuntimeError("provider down")

    owner, o_csrf, oid = _user(make_client)
    c = owner
    c.eng.translator = Translator(c.eng.s, providers=[Boom()])
    g = c.post("/api/chat/groups", json={"name": "Down", "lang": "en"}, headers=_h(o_csrf)).json()
    msg = c.post(
        f"/api/chat/dialogs/{g['id']}/messages",
        json={"type": "text", "text": "still here", "src_lang": "en"},
        headers=_h(o_csrf),
    ).json()
    assert msg["translation"] == "still here"
    assert msg["translation_status"] == "fallback"

    c.eng.translator = Translator(c.eng.s, providers=[StubProvider()])
    cmd = c.post(
        f"/api/chat/dialogs/{g['id']}/messages",
        json={"type": "text", "text": "translate to German", "src_lang": "en"},
        headers=_h(o_csrf),
    ).json()
    assert cmd["translation"] == "translate to German"
    assert cmd["translation_provider"] == "command_filter"


def test_ws_fanout_members_only(make_client):
    (owner, o_csrf, _oid), (member, m_csrf, mid), (outsider, _x_csrf, _xid) = _users(make_client, 3)
    g = owner.post("/api/chat/groups", json={"name": "WS"}, headers=_h(o_csrf)).json()
    gid = g["id"]
    owner.post(f"/api/chat/groups/{gid}/members", json={"user_id": mid}, headers=_h(o_csrf))

    otok = owner.get("/api/ws-token").json()["token"]
    mtok = member.get("/api/ws-token").json()["token"]
    xtok = outsider.get("/api/ws-token").json()["token"]
    with owner.websocket_connect("/ws/chat?t=" + otok) as ows, \
            member.websocket_connect("/ws/chat?t=" + mtok) as mws, \
            outsider.websocket_connect("/ws/chat?t=" + xtok) as xws:
        assert ows.receive_json()["type"] == "chat.hello"
        assert mws.receive_json()["type"] == "chat.hello"
        assert xws.receive_json()["type"] == "chat.hello"
        posted = owner.post(
            f"/api/chat/dialogs/{gid}/messages",
            json={"type": "text", "text": "only members", "src_lang": "en"},
            headers=_h(o_csrf),
        )
        assert posted.status_code == 201, posted.text

        def _next_message(ws):
            deadline = time.time() + 2
            while time.time() < deadline:
                ev = ws.receive_json()
                if ev.get("type") == "chat.message":
                    return ev
            raise AssertionError("no chat.message")

        oev = _next_message(ows)
        mev = _next_message(mws)
        assert oev["message"]["text"] == "only members"
        assert mev["message"]["text"] == "only members"
        time.sleep(0.15)
        xws.send_json({"type": "ping", "t": 1})
        ev = xws.receive_json()
        assert ev["type"] == "pong", ev


def test_admin_redacts_group_message_text():
    msg = Message(
        id="m1", dialog_id="g1", sender_id="u1", type="text",
        created_at=1.0, text="secret body", translation="[en→ru] secret body",
        translations={"ru": {"text": "[en→ru] secret body", "provider": "stub", "status": "ok"}},
    )
    redacted = msg.to_admin()
    blob = str(redacted)
    assert "secret" not in blob
    assert redacted["content_redacted"] is True
    public = msg.to_public()
    assert "secret" not in str(redact_for_admin(public))


def test_invalid_avatar_rejected(make_client):
    c = make_client()
    r = c.post("/api/chat/groups", json={"name": "X", "avatar": "javascript:alert(1)"})
    assert r.status_code == 400
    r = c.post("/api/chat/groups", json={"name": "X", "avatar": "http://example.com/a.png"})
    assert r.status_code == 400


def test_api_chat_status_includes_groups(make_client):
    body = make_client().get("/api/chat").json()
    assert "groups" in body["partial"]
    assert body["limits"]["group_max_members"] >= 2
