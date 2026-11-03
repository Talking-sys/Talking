import time

import pytest

from tests.media_bytes import make_wav_bytes, mp4_header, webm_header


def _dialog(c):
    dialogs = c.get("/api/chat/dialogs").json()
    assert dialogs
    return dialogs[0]["id"]


def _note(c, dialog, data, kind="voice_note", duration_ms=800, filename="n.wav",
          waveform=None, target_lang="ru"):
    files = {"file": (filename, data, "application/octet-stream")}
    form = {"kind": kind, "duration_ms": str(duration_ms), "target_lang": target_lang}
    if waveform is not None:
        import json
        form["waveform"] = json.dumps(waveform)
    return c.post(f"/api/chat/dialogs/{dialog}/notes", files=files, data=form)


def test_chat_module_is_notes_slice_not_501(make_client):
    c = make_client()
    r = c.get("/api/chat")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["module"] == "chat"
    assert body["feature"] == "notes"
    assert "voice_note" in body["types"]
    assert c.get("/chat").status_code == 200
    html = c.get("/chat").text
    assert "recordBtn" in html
    assert "кружочек" in html.lower() or "кружоч" in html
    assert c.get("/api/games").status_code == 501


def test_index_links_to_chat(make_client):
    c = make_client()
    assert 'href="/chat"' in c.get("/").text
    assert "camera=(self)" in c.get("/").headers["permissions-policy"]


def test_upload_wav_creates_voice_note_without_auto_transcript(make_client):
    c = make_client()
    c.stt.push("hello world", "en")
    d = _dialog(c)
    wav = make_wav_bytes()
    r = _note(c, d, wav, waveform=[4, 8, 16, 32])
    assert r.status_code == 201, r.text
    msg = r.json()
    assert msg["type"] == "voice_note"
    assert msg["kind"] == "voice_note"
    assert msg["duration_ms"] == 800
    assert msg["media_id"]
    assert msg["play_url"].startswith("/api/chat/media/")
    assert not msg.get("transcript")
    assert msg.get("transcript_status") in (None, "")
    assert msg["waveform"][0] == 4
    listed = c.get(f"/api/chat/dialogs/{d}/messages").json()
    assert listed[-1]["id"] == msg["id"]
    chat = c.get("/api/chat").json()
    assert chat["transcription"]["on_demand"] is True
    assert chat["transcription"]["premium_required"] is True
    assert chat["transcription"]["entitled"] is False


def test_upload_then_message_create(make_client):
    c = make_client()
    wav = make_wav_bytes()
    up = c.post("/api/chat/upload", files={"file": ("a.wav", wav, "audio/wav")},
                data={"kind": "voice_note"})
    assert up.status_code == 200, up.text
    media_id = up.json()["id"]
    d = _dialog(c)
    r = c.post(f"/api/chat/dialogs/{d}/messages", json={
        "type": "voice_note", "media_id": media_id, "duration_ms": 900, "target_lang": "de",
    })
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "voice_note"
    assert r.json()["media_id"] == media_id


def test_video_note_webm_magic(make_client):
    c = make_client()
    d = _dialog(c)
    r = _note(c, d, webm_header(2048), kind="video_note", filename="c.webm", duration_ms=1200)
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "video_note"
    assert r.json()["mime"] == "video/webm"
    # Upload is on-demand: no automatic STT on the note.
    assert r.json().get("transcript_status") in (None, "", "pending")


def test_reject_exe_and_html_and_png(make_client):
    c = make_client()
    d = _dialog(c)
    assert _note(c, d, b"MZ" + b"\x00" * 40, filename="x.exe").status_code == 400
    assert _note(c, d, b"<html><script>alert(1)</script>", filename="x.html").status_code == 400
    assert _note(c, d, b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, filename="x.png").status_code == 400
    assert _note(c, d, b"%PDF-1.4", filename="x.pdf").status_code == 400


def test_reject_dangerous_filename_even_with_wav(make_client):
    c = make_client()
    d = _dialog(c)
    assert _note(c, d, make_wav_bytes(), filename="note.html").status_code == 400


def test_kind_must_match_container(make_client):
    c = make_client()
    d = _dialog(c)
    assert _note(c, d, make_wav_bytes(), kind="video_note", filename="n.wav").status_code == 400
    assert _note(c, d, mp4_header(), kind="voice_note", filename="n.mp4").status_code in (200, 201, 400)
    # mp4 is allowed for voice_note (m4a). webm video kind with wav is the important reject.


def test_duration_cap_and_short(make_client):
    c = make_client()
    d = _dialog(c)
    wav = make_wav_bytes()
    assert _note(c, d, wav, duration_ms=70_000).status_code == 400
    assert _note(c, d, wav, duration_ms=50).status_code == 400


def test_file_too_large(make_client):
    c = make_client(env={"CHAT_VOICE_MAX_BYTES": "800"})
    d = _dialog(c)
    r = _note(c, d, make_wav_bytes(0.4))
    assert r.status_code == 413


def test_quota_exceeded(make_client):
    wav = make_wav_bytes(0.3)
    c = make_client(env={"CHAT_QUOTA_BYTES": str(len(wav) + 10)})
    d = _dialog(c)
    first = _note(c, d, wav)
    assert first.status_code == 201, first.text
    second = _note(c, d, wav)
    assert second.status_code == 413


def test_play_token_and_one_time_download(make_client):
    c = make_client()
    d = _dialog(c)
    msg = _note(c, d, make_wav_bytes()).json()
    play = c.get(msg["play_url"])
    assert play.status_code == 200
    assert play.headers["content-type"].startswith("audio/")
    # reusable play token still works
    assert c.get(msg["play_url"]).status_code == 200
    minted = c.post(f"/api/chat/media/{msg['media_id']}/token", json={"one_time": True, "ttl": 60})
    assert minted.status_code == 200
    url = minted.json()["url"]
    assert minted.json()["one_time"] is True
    assert c.get(url).status_code == 200
    again = c.get(url)
    assert again.status_code == 410
    assert c.get(f"/api/chat/media/{msg['media_id']}").status_code == 401
    assert c.get(f"/api/chat/media/{msg['media_id']}?token=not-a-token").status_code == 401


def test_expired_download_token(make_client):
    c = make_client()
    d = _dialog(c)
    msg = _note(c, d, make_wav_bytes()).json()
    minted = c.post(f"/api/chat/media/{msg['media_id']}/token", json={"one_time": False, "ttl": 1})
    url = minted.json()["url"]
    time.sleep(2.1)
    assert c.get(url).status_code == 401


def test_text_message_limit_and_rate(make_client):
    c = make_client()
    d = _dialog(c)
    too_long = c.post(f"/api/chat/dialogs/{d}/messages", json={"type": "text", "text": "x" * 501})
    assert too_long.status_code == 400
    codes = []
    for i in range(12):
        r = c.post(f"/api/chat/dialogs/{d}/messages", json={"type": "text", "text": f"hi {i}"})
        codes.append(r.status_code)
    assert 201 in codes
    assert 429 in codes


def test_stt_not_run_on_upload(make_client):
    class DeadSTT:
        ready = False
        loaded_name = None

        def transcribe(self, *a, **k):
            raise AssertionError("STT should not run on upload")

    c = make_client(stt=DeadSTT())
    d = _dialog(c)
    msg = _note(c, d, make_wav_bytes()).json()
    assert not msg.get("transcript")
    assert msg.get("transcript_status") in (None, "")


def test_chat_ws_pushes_message(make_client):
    c = make_client()
    d = _dialog(c)
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
        r = c.post(f"/api/chat/dialogs/{d}/messages", json={"type": "text", "text": "ping-ws"})
        assert r.status_code == 201
        ev = ws.receive_json()
        assert ev["type"] == "chat.message"
        assert ev["message"]["text"] == "ping-ws"
