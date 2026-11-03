from tests.media_bytes import make_wav_bytes, webm_header


def test_record_transcribe_translate_no_persist(make_client):
    c = make_client()
    c.stt.push("hello world", "en")
    wav = make_wav_bytes()
    r = c.post(
        "/api/translate/record",
        files={"file": ("n.wav", wav, "audio/wav")},
        data={"source_lang": "auto", "target_lang": "ru", "tts": "1", "duration_ms": "800"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["text"] == "hello world"
    assert body["translation"] == "[en→ru] hello world"
    assert body["provider"] == "stub"
    assert body["audio_retained"] is False
    assert body["message"] is None
    assert body["speak"]["text"] == body["translation"]
    d = c.get("/api/chat/dialogs").json()[0]["id"]
    msgs = c.get(f"/api/chat/dialogs/{d}/messages").json()
    assert msgs == []


def test_record_attaches_voice_note_to_chat(make_client):
    c = make_client()
    c.stt.push("good morning", "en")
    d = c.get("/api/chat/dialogs").json()[0]["id"]
    wav = make_wav_bytes()
    r = c.post(
        "/api/translate/record",
        files={"file": ("n.wav", wav, "audio/wav")},
        data={
            "source_lang": "auto",
            "target_lang": "de",
            "dialog_id": d,
            "duration_ms": "900",
            "waveform": "[4,8,16]",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["audio_retained"] is True
    msg = body["message"]
    assert msg["type"] == "voice_note"
    assert msg["transcript"] == "good morning"
    assert msg["translation"] == "[en→de] good morning"
    assert msg["transcript_status"] == "ok"
    assert msg["play_url"].startswith("/api/chat/media/")
    listed = c.get(f"/api/chat/dialogs/{d}/messages").json()
    assert listed[-1]["id"] == msg["id"]


def test_record_rejects_unknown_lang_and_exe(make_client):
    c = make_client()
    wav = make_wav_bytes()
    bad = c.post(
        "/api/translate/record",
        files={"file": ("n.wav", wav, "audio/wav")},
        data={"target_lang": "not-a-lang", "duration_ms": "800"},
    )
    assert bad.status_code == 400
    exe = c.post(
        "/api/translate/record",
        files={"file": ("x.exe", b"MZ" + b"\x00" * 40, "application/octet-stream")},
        data={"target_lang": "ru", "duration_ms": "800"},
    )
    assert exe.status_code == 400


def test_record_undecodable_webm_without_pcm(make_client):
    c = make_client()
    r = c.post(
        "/api/translate/record",
        files={"file": ("n.webm", webm_header(2048), "video/webm")},
        data={"target_lang": "ru", "duration_ms": "800"},
    )
    # Fake webm has no PCM; ffmpeg may or may not invent silence.
    assert r.status_code in (200, 201, 422)


def test_record_rate_limit(make_client):
    c = make_client(env={"VOICE_TRANSLATE_RECORD_PER_MIN": "2"})
    wav = make_wav_bytes()
    codes = []
    for i in range(5):
        r = c.post(
            "/api/translate/record",
            files={"file": ("n.wav", wav, "audio/wav")},
            data={"target_lang": "ru", "duration_ms": "800"},
        )
        codes.append(r.status_code)
    assert 200 in codes
    assert 429 in codes


def test_record_chat_ws_push(make_client):
    c = make_client()
    c.stt.push("ping", "en")
    d = c.get("/api/chat/dialogs").json()[0]["id"]
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
        r = c.post(
            "/api/translate/record",
            files={"file": ("n.wav", make_wav_bytes(), "audio/wav")},
            data={"target_lang": "ru", "dialog_id": d, "duration_ms": "800"},
        )
        assert r.status_code == 201, r.text
        ev = ws.receive_json()
        assert ev["type"] == "chat.message"
        assert ev["message"]["type"] == "voice_note"
        assert ev["message"]["transcript"] == "ping"
