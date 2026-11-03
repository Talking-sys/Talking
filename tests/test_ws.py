import pytest
from starlette.websockets import WebSocketDisconnect

from talking.stt import STTResult
from talking.vad import float_to_pcm16
import numpy as np


def _until(ws, typ, n=40):
    for _ in range(n):
        ev = ws.receive_json()
        if ev.get("type") == typ:
            return ev
    raise AssertionError(f"did not receive {typ}")


def _connect(c):
    tok = c.get("/api/ws-token").json()["token"]
    return c.websocket_connect("/ws?t=" + tok)


def _tone(seconds=0.6, sr=16000):
    t = np.sin(np.arange(int(sr * seconds)) * 2 * np.pi * 220 / sr).astype(np.float32) * 0.3
    return float_to_pcm16(t)


def test_ws_translate_flow(make_client):
    c = make_client()
    c.stt.push("hello world", "en")
    with _connect(c) as ws:
        cfg = _until(ws, "config")
        assert "session_id" in cfg
        ws.send_json({"type": "config", "target_lang": "ru", "client_vad": True, "pause_ms": 800})
        ws.send_json({"type": "start"})
        assert _until(ws, "started")
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        src = _until(ws, "source")
        assert src["text"] == "hello world"
        tr = _until(ws, "translation")
        assert tr["translation"] == "[en→ru] hello world"
        assert tr["provider"] == "stub"


def test_ws_voice_command(make_client):
    c = make_client()
    c.stt.push("переведи на немецкий", "ru")
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "start"})
        _until(ws, "started")
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        cmd = _until(ws, "command")
        assert cmd["target_lang"] == "de"


def test_ws_bidirectional(make_client):
    c = make_client()
    c.stt.queue = [
        STTResult("привет", "ru", 0.99, 0.6, 1),
        STTResult("hello", "en", 0.99, 0.6, 1),
    ]
    with _connect(c) as ws:
        _until(ws, "config")
        ws.send_json({"type": "config", "target_lang": "en", "partner_lang": "ru", "client_vad": True})
        ws.send_json({"type": "start"})
        _until(ws, "started")
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        src1 = _until(ws, "source")
        assert src1["tgt_lang"] == "en"
        _until(ws, "translation")
        ws.send_bytes(_tone())
        ws.send_json({"type": "eou"})
        src2 = _until(ws, "source")
        assert src2["tgt_lang"] == "ru"


def test_ws_requires_valid_token(make_client):
    c = make_client()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws?t=not-a-token"):
            pass
