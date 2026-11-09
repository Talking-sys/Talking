"""Backend tests for Telegram-like message editing."""
from __future__ import annotations

import time

from talking.chat.edits import (
    EditWindowExpired,
    InvalidText,
    NotAuthor,
    UneditableKind,
    apply_text_edit,
    check_can_edit,
    validate_edit_text,
)
from talking.chat.messages import MessageStore
from talking.chat.translation_cache import MessageTranslationCache
from talking.unfurl.fetch import RawResponse, SafeFetcher
from talking.unfurl.service import UnfurlService
from talking.unfurl.ssrf import SSRFError

from tests.test_unfurl_api import public_gai
from tests.test_unfurl_parser import OG_HTML, PLAIN_HTML


def _post(c, text, **extra):
    body = {"text": text, **extra}
    r = c.post("/api/chat/messages", json=body)
    assert r.status_code == 200, r.text
    return r.json()["message"]


def test_author_can_edit_sets_edited_at_and_hides_history(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "hello")
    r = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "hello there"})
    assert r.status_code == 200
    body = r.json()["message"]
    assert body["text"] == "hello there"
    assert body["edited"] is True
    assert body["edit_count"] == 1
    assert body["edited_at"] >= msg["created_at"]
    assert "original_text" not in body
    assert "revisions" not in body
    listed = c.get("/api/chat/messages").json()
    assert listed["edit_window_s"] == 48 * 3600
    assert listed["messages"][-1]["edited"] is True
    stored = c.app_.state.messages.get(msg["id"])
    assert stored.original_text == "hello"
    assert stored.revisions[0]["text"] == "hello"


def test_second_edit_keeps_original_and_appends_revision(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "v1")
    c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "v2"})
    c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "v3"})
    stored = c.app_.state.messages.get(msg["id"])
    assert stored.original_text == "v1"
    assert [x["text"] for x in stored.revisions] == ["v1", "v2"]
    assert stored.text == "v3"
    assert stored.edit_count == 2
    public = stored.to_dict()
    assert "original_text" not in public
    assert "revisions" not in public


def test_identical_text_is_noop(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "same")
    r = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "same"})
    assert r.status_code == 200
    assert r.json()["changed"] is False
    assert r.json()["message"]["edited"] is False
    assert r.json()["message"]["edited_at"] is None


def test_not_author_403(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    foreign = c.app_.state.messages.add(text="secret", sender_id="alice")
    r = c.patch(f"/api/chat/messages/{foreign.id}", json={"text": "nope"})
    assert r.status_code == 403
    assert "author" in r.json()["detail"]


def test_sender_id_spoofing_403(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    assert c.post("/api/chat/messages", json={"text": "hi", "sender_id": "alice"}).status_code == 403
    msg = _post(c, "mine")
    r = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "nope", "sender_id": "bob"})
    assert r.status_code == 403
    assert "spoof" in r.json()["detail"]


def test_unknown_id_404(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    r = c.patch("/api/chat/messages/does-not-exist", json={"text": "x"})
    assert r.status_code == 404


def test_edit_window_409(make_client):
    store = MessageStore()
    old = store.add(text="stale", created_at=time.time() - 49 * 3600)
    c = make_client(env={"UNFURL_STUB": "1", "CHAT_EDIT_WINDOW_S": str(48 * 3600)}, messages=store)
    r = c.patch(f"/api/chat/messages/{old.id}", json={"text": "too late"})
    assert r.status_code == 409
    assert "window" in r.json()["detail"]


def test_unlimited_window_when_zero(make_client):
    store = MessageStore()
    old = store.add(text="old", created_at=time.time() - 10 * 24 * 3600)
    c = make_client(env={"UNFURL_STUB": "1", "CHAT_EDIT_WINDOW_S": "0"}, messages=store)
    r = c.patch(f"/api/chat/messages/{old.id}", json={"text": "still ok"})
    assert r.status_code == 200
    assert r.json()["message"]["text"] == "still ok"


def test_empty_and_too_long_422(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "hi")
    assert c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "   "}).status_code == 422
    assert c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "x" * 501}).status_code == 422
    assert c.patch(f"/api/chat/messages/{msg['id']}", json={}).status_code == 422
    assert c.patch(f"/api/chat/messages/{msg['id']}", content="not-json").status_code == 400


def test_uneditable_kind_422(make_client):
    store = MessageStore()
    note = store.add(text="waveform", kind="voice_note")
    c = make_client(env={"UNFURL_STUB": "1"}, messages=store)
    r = c.patch(f"/api/chat/messages/{note.id}", json={"text": "caption"})
    assert r.status_code == 422
    assert "editable" in r.json()["detail"]


def test_unfurl_refresh_when_url_changes(make_client):
    pages = {
        "https://news.example/a": RawResponse(
            200, {"content-type": "text/html"}, OG_HTML.encode(), "https://news.example/a"
        ),
        "https://example.com/p": RawResponse(
            200, {"content-type": "text/html"}, PLAIN_HTML.encode(), "https://example.com/p"
        ),
    }

    def hop(**kw):
        url = kw["url"]
        if url not in pages:
            raise SSRFError("blocked")
        return pages[url]

    svc = UnfurlService(fetcher=SafeFetcher(getaddrinfo=public_gai, hop=hop), stub=False)
    c = make_client(unfurl=svc)
    msg = _post(c, "see https://news.example/a")
    assert msg["preview"]["title"] == "OG Title"
    r = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "now https://example.com/p"})
    assert r.status_code == 200
    preview = r.json()["message"]["preview"]
    assert preview["title"] == "Plain Title"
    assert r.json()["message"]["urls"] == ["https://example.com/p"]


def test_unfurl_cleared_when_urls_removed(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "look https://example.com/story")
    assert msg["preview"]
    r = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "no link anymore"})
    assert r.json()["message"]["preview"] is None
    assert r.json()["message"]["urls"] == []


def test_unfurl_kept_when_first_url_unchanged(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "look https://example.com/story")
    title = msg["preview"]["title"]
    r = c.patch(
        f"/api/chat/messages/{msg['id']}",
        json={"text": "still https://example.com/story plus words"},
    )
    assert r.json()["message"]["preview"]["title"] == title


def test_translation_cache_invalidated_on_edit(make_client):
    store = MessageStore()
    msg = store.add(
        text="hello",
        translation="[en→ru] hello",
        src_lang="en",
        tgt_lang="ru",
        translation_provider="stub",
    )
    c = make_client(env={"UNFURL_STUB": "1"}, messages=store)
    cache: MessageTranslationCache = c.app_.state.chat_translations
    cache.store(msg.id, "hello", "en", "ru", "[en→ru] hello")
    assert cache.get("hello", "en", "ru") == "[en→ru] hello"
    r = c.patch(f"/api/chat/messages/{msg.id}", json={"text": "hello world"})
    body = r.json()["message"]
    assert body["translation"] is None
    assert body["src_lang"] is None
    assert cache.get("hello", "en", "ru") is None
    assert cache.get_for_message(msg.id) is None


def test_edit_rate_limit_429(make_client):
    from talking.security import TokenBucket

    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "rate me")
    ok = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "once"})
    assert ok.status_code == 200
    # BaseHTTPMiddleware can run the endpoint more than once per TestClient
    # call, so drain whatever keys the first PATCH used rather than assuming
    # burst=1 maps to one HTTP round-trip.
    lim: TokenBucket = c.app_.state.chat_edit_limiter
    for key in list(lim._tokens):
        lim._tokens[key] = 0
    blocked = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "twice"})
    assert blocked.status_code == 429


def test_edit_token_bucket_exhausts():
    from talking.security import TokenBucket

    bucket = TokenBucket(1, burst=1)
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is False


def test_ws_broadcasts_message_edited(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    msg = _post(c, "live")
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
        r = c.patch(f"/api/chat/messages/{msg['id']}", json={"text": "live edited"})
        assert r.status_code == 200
        ev = ws.receive_json()
        assert ev["type"] == "message_edited"
        assert ev["message"]["text"] == "live edited"
        assert ev["message"]["edited"] is True
        assert "original_text" not in ev["message"]


def test_policy_helpers_direct():
    store = MessageStore()
    msg = store.add(text="x", sender_id="a")
    check_can_edit(msg, actor_id="a", now=msg.created_at + 1, window_s=60)
    try:
        check_can_edit(msg, actor_id="b", now=msg.created_at + 1, window_s=60)
        assert False, "expected NotAuthor"
    except NotAuthor:
        pass
    msg.kind = "video_note"
    try:
        check_can_edit(msg, actor_id="a", now=msg.created_at + 1, window_s=60)
        assert False, "expected UneditableKind"
    except UneditableKind:
        pass
    msg.kind = "text"
    try:
        check_can_edit(msg, actor_id="a", now=msg.created_at + 100, window_s=10)
        assert False, "expected EditWindowExpired"
    except EditWindowExpired:
        pass
    assert apply_text_edit(msg, "x", now=msg.created_at + 1) is False
    assert apply_text_edit(msg, "y", now=msg.created_at + 1) is True
    try:
        validate_edit_text("  ", 500)
        assert False, "expected InvalidText"
    except InvalidText:
        pass
