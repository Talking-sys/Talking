import threading
import time

import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from talking.vad import float_to_pcm16
from talking.voice_translate.session import LiveTranslateSession
from talking.voice_translate.stream_stt import FakeStreamingSTT


def _until(ws, typ, n=50):
    for _ in range(n):
        ev = ws.receive_json()
        if ev.get("type") == typ:
            return ev
    raise AssertionError(f"did not receive {typ}")


def _connect(c):
    tok = c.get("/api/ws-token").json()["token"]
    return c.websocket_connect("/ws/translate/live?t=" + tok)


def _tone(seconds=0.6, sr=16000):
    t = np.sin(np.arange(int(sr * seconds)) * 2 * np.pi * 220 / sr).astype(np.float32) * 0.3
    return float_to_pcm16(t)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _install(c, stt, clock=None, max_s=None, max_pending_s=None):
    def factory(eng, emit, uid):
        return LiveTranslateSession(
            eng, emit, uid, stt=stt, clock=clock, max_s=max_s, max_pending_s=max_pending_s,
        )
    c.app_.state.live_session_factory = factory
    return stt


def test_live_status_and_page(make_client):
    c = make_client()
    r = c.get("/api/translate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["modes"] == ["live", "record"]
    assert body["live_premium"] is False
    assert body["live_allowed"] is True
    assert body["streaming"] == "vad_windows"
    assert "stub" in body["providers"]
    html = c.get("/translate")
    assert html.status_code == 200
    assert "Живой перевод" in html.text
    assert "Запись" in html.text
    assert "recordBtn" in html.text
    assert 'href="/translate"' in c.get("/").text


def test_live_ws_partial_then_final(make_client):
    c = make_client()
    fake = FakeStreamingSTT()
    fake.push("hel", "en", partial=True)
    fake.push("hello world", "en", partial=False)
    _install(c, fake)
    with _connect(c) as ws:
        cfg = _until(ws, "config")
        assert "session_id" in cfg
        ws.send_json({"type": "config", "source_lang": "auto", "target_lang": "ru", "partner_lang": "en", "tts": False})
        ws.send_json({"type": "start"})
        assert _until(ws, "started")
        ws.send_bytes(_tone())
        part = _until(ws, "partial")
        assert part["text"] == "hel"
        assert part["partial"] is True
        assert part["translation"] == "[en→ru] hel"
        assert part["speaker"] == "a"
        ws.send_json({"type": "eou"})
        fin = _until(ws, "final")
        assert fin["text"] == "hello world"
        assert fin["final"] is True
        assert fin["translation"] == "[en→ru] hello world"
        assert fin["provider"] == "stub"


def test_live_auto_detect_two_sides_and_swap(make_client):
    c = make_client()
    fake = FakeStreamingSTT()
    fake.push("привет", "ru", partial=False)
    fake.push("hello", "en", partial=False)
    fake.push("thanks", "en", partial=False)
    _install(c, fake)
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "config", "source_lang": "auto", "target_lang": "en", "partner_lang": "ru", "tts": False})
        ws.send_json({"type": "start"})
        _until(ws, "started")
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        a = _until(ws, "final")
        assert a["src_lang"] == "ru" and a["tgt_lang"] == "en" and a["speaker"] == "a"
        assert a["translation"] == "[ru→en] привет"
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        b = _until(ws, "final")
        assert b["src_lang"] == "en" and b["tgt_lang"] == "ru" and b["speaker"] == "b"
        ws.send_json({"type": "swap"})
        sw = _until(ws, "swap")
        assert sw["target_lang"] == "ru"
        assert sw["source_lang"] == "auto"
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        c2 = _until(ws, "final")
        # After swap, target is ru so English speech is side A → ru.
        assert c2["tgt_lang"] == "ru"
        assert c2["speaker"] == "a"


def test_live_session_limit(make_client):
    c = make_client()
    clock = Clock(1000.0)
    fake = FakeStreamingSTT()
    _install(c, fake, clock=clock, max_s=1)
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "start"})
        _until(ws, "started")
        clock.t = 1002.0
        ws.send_bytes(_tone(0.2))
        lim = _until(ws, "limit")
        assert lim["reason"] == "max_session_s"
        assert lim["max_s"] == 1


def test_live_backpressure_drops_when_stt_blocked(make_client):
    c = make_client()

    class BlockingSTT:
        def __init__(self):
            self.gate = threading.Event()
            self.started = threading.Event()

        def reset(self):
            return None

        def feed(self, pcm, language=None):
            self.started.set()
            self.gate.wait(3)
            return []

        def flush(self, language=None):
            return []

    slow = BlockingSTT()
    _install(c, slow, max_pending_s=0.4)
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "start"})
        _until(ws, "started")
        ws.send_bytes(_tone(0.3))
        assert slow.started.wait(2)
        for _ in range(8):
            ws.send_bytes(_tone(0.3))
        bp = _until(ws, "backpressure")
        assert bp["dropped_frames"] >= 1
        slow.gate.set()


def test_live_requires_valid_token(make_client):
    c = make_client()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/translate/live?t=not-a-token"):
            pass


def test_live_premium_hook_denies_when_gated_and_stub(make_client):
    c = make_client(env={"VOICE_TRANSLATE_LIVE_PREMIUM": "1"})
    st = c.get("/api/translate").json()
    assert st["live_premium"] is True
    assert st["live_allowed"] is False
    tok = c.get("/api/ws-token").json()["token"]
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/translate/live?t=" + tok) as ws:
            ev = ws.receive_json()
            assert ev["type"] == "error"
            assert ev["code"] == "not_entitled"
            ws.receive_json()


def test_live_premium_hook_allows_paid_tier(make_client):
    c = make_client(env={"VOICE_TRANSLATE_LIVE_PREMIUM": "1"})

    class Paid:
        def status(self):
            return {"enabled": True, "mode": "live", "url": "http://x", "modules": ["licensing"]}

        def license_status(self, user_id):
            return {"ok": True, "tier": "paid", "entitlements": ["live_translate"]}

    c.eng.commercial = Paid()
    st = c.get("/api/translate").json()
    assert st["live_allowed"] is True
    fake = FakeStreamingSTT()
    fake.push("hi", "en", partial=False)
    _install(c, fake)
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "start"})
        assert _until(ws, "started")


def test_live_tts_event_on_final(make_client):
    c = make_client()
    fake = FakeStreamingSTT()
    fake.push("hello", "en", partial=False)
    _install(c, fake)
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "config", "target_lang": "ru", "tts": True})
        ws.send_json({"type": "start"})
        _until(ws, "started")
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        fin = _until(ws, "final")
        assert fin["translation"]
        speak = _until(ws, "speak_browser")
        assert speak["text"] == fin["translation"]
        assert speak["lang"] == "ru"
        time.sleep(0.01)
