"""Premium on-demand voice/video-note transcription."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from talking.chat.models import Message, redact_for_admin
from talking.chat.transcribe import (
    DEFAULT_PROVIDER,
    FasterWhisperProvider,
    OpenAICompatibleProvider,
    build_provider,
)
from talking.commercial.client import ENTITLEMENT_VOICE_TRANSCRIPTION, CommercialClient
from talking.stt import FakeSTTEngine
from tests.media_bytes import make_wav_bytes, webm_header
from tests.test_chat_notes import _dialog, _note

PREMIUM = {"COMMERCIAL_DEV_ENTITLEMENTS": ENTITLEMENT_VOICE_TRANSCRIPTION}


def _wait_done(c, mid, timeout=5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = c.get(f"/api/chat/messages/{mid}/transcript")
        last = r.json()
        if last.get("status") in ("done", "failed"):
            return last
        time.sleep(0.04)
    raise AssertionError(f"timeout waiting for transcript: {last}")


def test_non_premium_gets_403(make_client):
    c = make_client()
    d = _dialog(c)
    mid = _note(c, d, make_wav_bytes()).json()["id"]
    r = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "premium_required"
    assert detail["entitlement"] == ENTITLEMENT_VOICE_TRANSCRIPTION
    assert detail["source"] in ("stub", "cache")
    html = c.get("/chat").text
    assert "transcribe-btn" in html or "Расшифровать" in html or "chat.js" in html


def test_dev_premium_transcribes_wav(make_client):
    c = make_client(env=PREMIUM)
    c.stt.push("hello world", "en")
    d = _dialog(c)
    msg = _note(c, d, make_wav_bytes(), waveform=[4, 8, 16]).json()
    assert not msg.get("transcript")
    r = c.post(f"/api/chat/messages/{msg['id']}/transcribe", json={"target_lang": "ru"})
    assert r.status_code == 200, r.text
    body = _wait_done(c, msg["id"])
    assert body["status"] == "done"
    assert body["transcript"] == "hello world"
    assert body["translation"] == "[en→ru] hello world"
    assert body["language"] == "en"
    assert body["provider"] == DEFAULT_PROVIDER
    assert body["model"]
    assert body["created_at"]
    listed = c.get(f"/api/chat/dialogs/{d}/messages").json()[-1]
    assert listed["transcript"] == "hello world"
    assert listed["transcript_status"] == "done"
    assert listed["transcript_provider"] == DEFAULT_PROVIDER
    chat = c.get("/api/chat").json()["transcription"]
    assert chat["entitled"] is True
    assert chat["source"] == "dev_override"


def test_idempotent_cached_second_tap(make_client):
    c = make_client(env=PREMIUM)
    d = _dialog(c)
    mid = _note(c, d, make_wav_bytes()).json()["id"]
    c.post(f"/api/chat/messages/{mid}/transcribe")
    first = _wait_done(c, mid)
    n = len(c.stt.calls)
    again = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert again.status_code == 200
    assert again.json()["status"] == "done"
    assert again.json()["transcript"] == first["transcript"]
    third = c.get(f"/api/chat/messages/{mid}/transcript")
    assert third.json()["status"] == "done"
    assert len(c.stt.calls) == n


def test_pending_job_not_restarted(make_client):
    gate = threading.Event()

    class BlockingSTT(FakeSTTEngine):
        def transcribe(self, audio, language=None):
            gate.wait(timeout=4)
            return super().transcribe(audio, language)

    stt = BlockingSTT(default_text="later")
    c = make_client(stt=stt, env=PREMIUM)
    mid = _note(c, _dialog(c), make_wav_bytes()).json()["id"]
    first = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    second = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert second.json()["status"] == "pending"
    assert len(stt.calls) == 0
    gate.set()
    body = _wait_done(c, mid)
    assert body["status"] == "done"
    assert body["transcript"] == "later"
    assert len(stt.calls) == 1


def test_commercial_unreachable_fail_closed(make_client):
    c = make_client(env={
        "COMMERCIAL_API_URL": "http://127.0.0.1:1",
        "COMMERCIAL_ENTITLEMENT_CACHE_TTL": "0",
        "COMMERCIAL_API_TIMEOUT": "0.2",
    })
    mid = _note(c, _dialog(c), make_wav_bytes()).json()["id"]
    r = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "premium_required"
    assert r.json()["detail"]["source"] == "unreachable"


def test_commercial_api_grants_entitlement(make_client, monkeypatch):
    def fake_get(self, path, default_resource):
        return {"ok": True, "entitlements": [ENTITLEMENT_VOICE_TRANSCRIPTION], "tier": "paid"}

    monkeypatch.setattr(CommercialClient, "get", fake_get)
    c = make_client(env={
        "COMMERCIAL_API_URL": "http://commercial.test",
        "COMMERCIAL_ENTITLEMENT_CACHE_TTL": "0",
    })
    c.stt.push("granted", "en")
    mid = _note(c, _dialog(c), make_wav_bytes()).json()["id"]
    r = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert r.status_code == 200, r.text
    body = _wait_done(c, mid)
    assert body["transcript"] == "granted"
    assert c.get("/api/chat").json()["transcription"]["source"] == "commercial"


def test_commercial_paid_tier_implies_voice_transcription(make_client, monkeypatch):
    def fake_get(self, path, default_resource):
        return {"ok": True, "tier": "paid"}

    monkeypatch.setattr(CommercialClient, "get", fake_get)
    c = make_client(env={"COMMERCIAL_API_URL": "http://commercial.test"})
    assert c.app_.state.engine.commercial.has_entitlement("local", ENTITLEMENT_VOICE_TRANSCRIPTION)


def test_entitlement_cache(make_client, monkeypatch):
    calls = {"n": 0}

    def fake_get(self, path, default_resource):
        calls["n"] += 1
        return {"ok": True, "entitlements": [ENTITLEMENT_VOICE_TRANSCRIPTION], "tier": "paid"}

    monkeypatch.setattr(CommercialClient, "get", fake_get)
    c = make_client(env={
        "COMMERCIAL_API_URL": "http://commercial.test",
        "COMMERCIAL_ENTITLEMENT_CACHE_TTL": "30",
    })
    com = c.app_.state.engine.commercial
    assert com.has_entitlement("local", ENTITLEMENT_VOICE_TRANSCRIPTION)
    assert com.has_entitlement("local", ENTITLEMENT_VOICE_TRANSCRIPTION)
    assert calls["n"] == 1


def test_ffmpeg_absent_webm_fails(make_client, monkeypatch):
    monkeypatch.setattr("talking.chat.transcribe.ffmpeg_available", lambda: False)
    monkeypatch.setattr("talking.chat.notes.ffmpeg_available", lambda: False)
    c = make_client(env=PREMIUM)
    d = _dialog(c)
    mid = _note(c, d, webm_header(2048), kind="video_note", filename="c.webm", duration_ms=1200).json()["id"]
    c.post(f"/api/chat/messages/{mid}/transcribe")
    body = _wait_done(c, mid)
    assert body["status"] == "failed"
    assert "ffmpeg" in (body.get("reason") or "").lower()
    assert not body.get("transcript")


def test_stt_unavailable_fails_job(make_client):
    class DeadSTT:
        ready = False
        loaded_name = None

        def load(self, *a, **k):
            from talking.stt import STTUnavailable
            raise STTUnavailable("faster-whisper is not installed")

        def transcribe(self, *a, **k):
            raise AssertionError("should not transcribe")

    c = make_client(stt=DeadSTT(), env=PREMIUM)
    mid = _note(c, _dialog(c), make_wav_bytes()).json()["id"]
    c.post(f"/api/chat/messages/{mid}/transcribe")
    body = _wait_done(c, mid)
    assert body["status"] == "failed"
    assert "whisper" in (body.get("reason") or "").lower() or "stt" in (body.get("reason") or "").lower()


def test_duration_cap(make_client):
    c = make_client(env={**PREMIUM, "CHAT_TRANSCRIBE_MAX_MS": "500"})
    mid = _note(c, _dialog(c), make_wav_bytes(), duration_ms=800).json()["id"]
    r = c.post(f"/api/chat/messages/{mid}/transcribe")
    assert r.status_code == 400
    assert "cap" in r.json()["detail"]


def test_rate_limit_transcriptions(make_client):
    c = make_client(env={**PREMIUM, "CHAT_TRANSCRIBE_PER_MIN": "1"})
    d = _dialog(c)
    a = _note(c, d, make_wav_bytes()).json()["id"]
    b = _note(c, d, make_wav_bytes()).json()["id"]
    assert c.post(f"/api/chat/messages/{a}/transcribe").status_code == 200
    r = c.post(f"/api/chat/messages/{b}/transcribe")
    assert r.status_code == 429


def test_api_errors_not_found_and_not_a_note(make_client):
    c = make_client(env=PREMIUM)
    d = _dialog(c)
    assert c.post("/api/chat/messages/mdeadbeefdeadbe/transcribe").status_code == 404
    assert c.get("/api/chat/messages/mdeadbeefdeadbe/transcript").status_code == 404
    text = c.post(f"/api/chat/dialogs/{d}/messages", json={"type": "text", "text": "hi"}).json()
    r = c.post(f"/api/chat/messages/{text['id']}/transcribe")
    assert r.status_code == 400
    assert "voice" in r.json()["detail"]


def test_delete_removes_transcript_and_media(make_client):
    c = make_client(env=PREMIUM)
    uid = c.eng.store.get("user_id")
    d = _dialog(c)
    msg = _note(c, d, make_wav_bytes()).json()
    c.post(f"/api/chat/messages/{msg['id']}/transcribe")
    _wait_done(c, msg["id"])
    media_path = Path(c.app_.state.chat_media.get(uid, msg["media_id"]).path)
    assert media_path.is_file()
    gone = c.delete(f"/api/chat/messages/{msg['id']}")
    assert gone.status_code == 200
    assert c.get(f"/api/chat/messages/{msg['id']}/transcript").status_code == 404
    listed = c.get(f"/api/chat/dialogs/{d}/messages").json()
    assert all(m["id"] != msg["id"] for m in listed)
    assert not media_path.exists()


def test_retention_purges_transcript(make_client):
    c = make_client(env=PREMIUM)
    uid = c.eng.store.get("user_id")
    d = _dialog(c)
    msg = _note(c, d, make_wav_bytes()).json()
    c.post(f"/api/chat/messages/{msg['id']}/transcribe")
    _wait_done(c, msg["id"])
    store = c.app_.state.chat_store
    stored = store.get_message(uid, d, msg["id"])
    assert stored is not None
    stored.created_at = time.time() - 10
    store.update_message(uid, stored)
    c.app_.state.settings.chat_retention_seconds = 1
    listed = c.get(f"/api/chat/dialogs/{d}/messages").json()
    assert all(m["id"] != msg["id"] for m in listed)
    assert c.get(f"/api/chat/messages/{msg['id']}/transcript").status_code == 404


def test_admin_never_sees_transcript():
    msg = Message(
        id="m1", dialog_id="local", sender_id="local", type="voice_note",
        created_at=1.0, transcript="secret speech", translation="секрет",
        transcript_status="done", src_lang="en",
    )
    redacted = msg.to_admin()
    assert "secret speech" not in str(redacted)
    assert "секрет" not in str(redacted)
    assert redacted["transcript_redacted"] is True
    assert "transcript" not in redacted
    public = {"transcript": "secret speech", "id": "m1"}
    assert "secret" not in str(redact_for_admin(public))


def test_admin_health_does_not_leak_chat(make_client):
    c = make_client(env=PREMIUM)
    mid = _note(c, _dialog(c), make_wav_bytes()).json()["id"]
    c.post(f"/api/chat/messages/{mid}/transcribe")
    _wait_done(c, mid)
    health = c.get("/api/commercial/admin/health")
    assert health.status_code == 200
    blob = health.text.lower()
    assert "hello" not in blob
    assert "transcript" not in blob or health.json().get("disabled") is True


def test_ws_emits_transcript_event(make_client):
    c = make_client(env=PREMIUM)
    d = _dialog(c)
    mid = _note(c, d, make_wav_bytes()).json()["id"]
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        assert ws.receive_json()["type"] == "chat.hello"
        c.post(f"/api/chat/messages/{mid}/transcribe")
        seen = set()
        deadline = time.time() + 5
        while time.time() < deadline and ("chat.transcript" not in seen or "chat.message" not in seen):
            ev = ws.receive_json()
            seen.add(ev["type"])
            if ev["type"] == "chat.transcript":
                assert ev["message_id"] == mid
                assert ev["status"] in ("pending", "done", "failed")
            if ev["type"] == "chat.message" and ev["message"]["id"] == mid:
                if ev["message"].get("transcript_status") == "done":
                    seen.add("done-msg")
        assert "chat.transcript" in seen


def test_default_provider_is_local_faster_whisper(make_client):
    c = make_client()
    prov = build_provider(c.eng, c.eng.s)
    assert isinstance(prov, FasterWhisperProvider)
    assert prov.name == DEFAULT_PROVIDER
    cloud = OpenAICompatibleProvider()
    assert cloud.available() is False
    try:
        cloud.transcribe(__import__("numpy").zeros(1600, dtype="float32"))
        raise AssertionError("cloud STT must stay off")
    except Exception as e:
        assert "disabled" in str(e).lower() or "stt" in str(e).lower()


def test_legacy_on_upload_still_premium_gated(make_client):
    free = make_client(env={"CHAT_TRANSCRIBE_ON_UPLOAD": "1"})
    free.stt.push("nope", "en")
    msg = _note(free, _dialog(free), make_wav_bytes()).json()
    assert not msg.get("transcript")

    paid = make_client(env={**PREMIUM, "CHAT_TRANSCRIBE_ON_UPLOAD": "1"})
    paid.stt.push("auto", "en")
    msg = _note(paid, _dialog(paid), make_wav_bytes()).json()
    assert msg["transcript"] == "auto"
    assert msg["transcript_status"] in ("done", "ok")


def test_disabled_master_switch(make_client):
    c = make_client(env={**PREMIUM, "CHAT_TRANSCRIBE": "0"})
    mid = _note(c, _dialog(c), make_wav_bytes()).json()["id"]
    assert c.post(f"/api/chat/messages/{mid}/transcribe").status_code == 503


def test_has_entitlement_stub_and_override():
    deny = CommercialClient("")
    assert deny.has_entitlement("u1", ENTITLEMENT_VOICE_TRANSCRIPTION) is False
    allow = CommercialClient("", dev_entitlements=ENTITLEMENT_VOICE_TRANSCRIPTION, app_env="test")
    assert allow.has_entitlement("u1", ENTITLEMENT_VOICE_TRANSCRIPTION) is True
    prod = CommercialClient("", dev_premium=True, app_env="production")
    assert prod.has_entitlement("u1", ENTITLEMENT_VOICE_TRANSCRIPTION) is False
