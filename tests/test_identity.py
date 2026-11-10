"""Server-side identity: no anonymous users, no body spoofing, WS handshake."""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from talking.identity import LOCAL_USER_ID
from talking.settings_store import SettingsStore


def test_settings_store_does_not_mint_anonymous_users(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.get("user_id") == LOCAL_USER_ID
    assert not str(store.get("user_id")).startswith("anon-")


def test_engine_user_is_local(make_client):
    c = make_client()
    assert c.eng.store.get("user_id") == LOCAL_USER_ID
    listed = c.get("/api/chat/messages").json()
    assert listed["user_id"] == LOCAL_USER_ID
    posted = c.post("/api/chat/messages", json={"text": "hello from operator"}).json()["message"]
    assert posted["sender_id"] == LOCAL_USER_ID
    matching = c.post("/api/chat/messages", json={"text": "still me", "sender_id": LOCAL_USER_ID})
    assert matching.status_code == 200
    assert matching.json()["message"]["sender_id"] == LOCAL_USER_ID


def _reject_unauthenticated(client, path):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path) as ws:
            ws.send_json({"type": "ping", "t": 1})


def test_ws_rejects_unauthenticated_ping(make_client):
    c = make_client()
    for path in ("/ws", "/ws/chat", "/ws/translate/live"):
        _reject_unauthenticated(c, path)


def test_ws_rejects_missing_and_foreign_token(make_client):
    c = make_client()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws"):
            pass
    tok = c.get("/api/ws-token").json()["token"]
    # Tamper with the payload so HMAC fails.
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws?t=" + tok[:-2] + "aa"):
            pass


def test_authenticated_ws_ping_still_works(make_client):
    c = make_client()
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws?t=" + tok) as ws:
        ws.receive_json()  # config
        ws.receive_json()  # status
        ws.send_json({"type": "ping", "t": 42})
        ev = None
        for _ in range(10):
            item = ws.receive_json()
            if item.get("type") == "pong":
                ev = item
                break
        assert ev is not None
        assert ev["t"] == 42


def test_chat_hub_does_not_broadcast_to_star(make_client):
    c = make_client()
    hub = c.app_.state.chat_hub
    assert "*" not in hub._subs
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["user_id"] == LOCAL_USER_ID
        assert "*" not in hub._subs
        assert LOCAL_USER_ID in hub._subs


def test_playwright_artifacts_default_is_writable(monkeypatch):
    monkeypatch.delenv("TALKING_ARTIFACTS", raising=False)
    import importlib
    import tests.test_playwright_notes as notes
    import tests.test_playwright_transcription as transcribe
    import tests.test_playwright_translate as translate

    importlib.reload(notes)
    importlib.reload(transcribe)
    importlib.reload(translate)
    expected = Path(__file__).resolve().parent / "output"
    assert notes.ARTIFACTS == expected
    assert transcribe.ARTIFACTS == expected
    assert translate.ARTIFACTS == expected
    assert "/opt/cursor/artifacts" not in str(notes.ARTIFACTS)
