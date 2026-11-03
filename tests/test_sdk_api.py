"""In-process FastAPI tests for /api/sdk/v1."""
from __future__ import annotations

import base64
import io
import wave

from talking.languages import languages_payload
from talking.sdk_api import parse_sdk_api_keys
from talking.tts import TTSResult

KEY = "test-sdk-key-not-for-production"
APP = "demo"
ENV = {"SDK_API_KEYS": f"{APP}:{KEY}"}
HDR = {"Authorization": f"Bearer {KEY}", "X-Talking-App-Id": APP}


def _wav(seconds: float = 0.2, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    n = int(sr * seconds)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def test_parse_sdk_api_keys():
    pairs = parse_sdk_api_keys("demo:abc, other=xyz, baretoken, #comment")
    assert ("abc", "demo") in pairs
    assert ("xyz", "other") in pairs
    assert ("baretoken", "default") in pairs
    assert parse_sdk_api_keys("") == []


def test_sdk_disabled_without_keys(make_client):
    c = make_client(env={"SDK_API_KEYS": ""})
    r = c.get("/api/sdk/v1/languages")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "sdk_disabled"


def test_sdk_unauthorized(make_client):
    c = make_client(env=ENV)
    assert c.get("/api/sdk/v1/languages").status_code == 401
    r = c.get("/api/sdk/v1/languages", headers={"Authorization": "Bearer wrong-key"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_sdk_app_id_mismatch(make_client):
    c = make_client(env=ENV)
    r = c.get("/api/sdk/v1/languages", headers={"Authorization": f"Bearer {KEY}", "X-Talking-App-Id": "other"})
    assert r.status_code == 401


def test_sdk_languages_match_whisper_list(make_client):
    c = make_client(env=ENV)
    r = c.get("/api/sdk/v1/languages", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    codes = {x["code"] for x in body["languages"]}
    assert codes == {x["code"] for x in languages_payload()}
    assert "de" in codes and "ru" in codes and "ja" in codes
    assert r.headers.get("X-RateLimit-Limit")
    alt = c.get("/api/sdk/v1/languages", headers={"X-Talking-Api-Key": KEY})
    assert alt.status_code == 200


def test_sdk_info(make_client):
    c = make_client(env=ENV)
    r = c.get("/api/sdk/v1", headers=HDR)
    assert r.status_code == 200
    assert r.json()["api"] == "talking-sdk"
    assert r.json()["version"] == "v1"


def test_sdk_translate_text_stub(make_client):
    c = make_client(env=ENV)
    r = c.post("/api/sdk/v1/translate/text", headers=HDR,
               json={"text": "hello", "source": "en", "target": "ru"})
    assert r.status_code == 200
    body = r.json()
    assert body["translation"] == "[en→ru] hello"
    assert body["provider"] == "stub"
    assert body["source"] == "en"
    assert body["target"] == "ru"
    assert c.get("/api/history").json() == []


def test_sdk_translate_text_auto_and_identity(make_client):
    c = make_client(env=ENV)
    auto = c.post("/api/sdk/v1/translate/text", headers=HDR,
                  json={"text": "hola", "source": "auto", "target": "de"})
    assert auto.status_code == 200
    assert auto.json()["translation"] == "[auto→de] hola"
    same = c.post("/api/sdk/v1/translate/text", headers=HDR,
                  json={"text": "hello", "source": "en", "target": "en"})
    assert same.json()["translation"] == "hello"
    assert same.json()["provider"] == "identity"


def test_sdk_translate_validation(make_client):
    c = make_client(env={**ENV, "SDK_MAX_TEXT_CHARS": "8"})
    assert c.post("/api/sdk/v1/translate/text", headers=HDR,
                  json={"text": "   ", "target": "en"}).status_code == 400
    bad = c.post("/api/sdk/v1/translate/text", headers=HDR,
                 json={"text": "hello", "target": "not-a-lang"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "unsupported_language"
    big = c.post("/api/sdk/v1/translate/text", headers=HDR,
                 json={"text": "abcdefghijk", "target": "en"})
    assert big.status_code == 413
    assert big.json()["error"]["code"] == "payload_too_large"


def test_sdk_return_audio_without_tts(make_client):
    c = make_client(env=ENV)
    r = c.post("/api/sdk/v1/translate/text", headers=HDR,
               json={"text": "hi", "source": "en", "target": "de", "return_audio": True})
    assert r.status_code == 200
    assert r.json()["audio"] is None
    assert r.json()["audio_error"]


def test_sdk_return_audio_with_fake_tts(make_client):
    class FakeTTS:
        last_errors: list[str] = []

        def synth(self, text, lang):
            return TTSResult(b"ID3fake", "audio/mpeg", "fake", 3)

    c = make_client(env=ENV, tts=FakeTTS())
    r = c.post("/api/sdk/v1/translate/text", headers=HDR,
               json={"text": "hi", "source": "en", "target": "de", "return_audio": True})
    assert r.status_code == 200
    assert base64.b64decode(r.json()["audio"]) == b"ID3fake"
    assert r.json()["audio_mime"] == "audio/mpeg"
    assert r.json()["tts_ms"] == 3


def test_sdk_translate_audio_wav(make_client):
    c = make_client(env=ENV)
    c.stt.push("hello world", "en")
    r = c.post(
        "/api/sdk/v1/translate/audio",
        headers=HDR,
        files={"audio": ("clip.wav", _wav(), "audio/wav")},
        data={"format": "wav", "target": "ru"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "hello world"
    assert body["translation"] == "[en→ru] hello world"
    assert body["source"] == "en"


def test_sdk_translate_audio_json_base64(make_client):
    c = make_client(env=ENV)
    c.stt.push("bonjour", "fr")
    r = c.post(
        "/api/sdk/v1/translate/audio",
        headers=HDR,
        json={
            "audio_base64": base64.b64encode(_wav()).decode(),
            "format": "wav",
            "target": "en",
        },
    )
    assert r.status_code == 200
    assert r.json()["text"] == "bonjour"
    assert r.json()["translation"] == "[fr→en] bonjour"


def test_sdk_translate_audio_pcm_and_bad_format(make_client):
    c = make_client(env=ENV)
    pcm = b"\x00\x00" * 1600
    c.stt.push("pcm line", "en")
    ok = c.post(
        "/api/sdk/v1/translate/audio",
        headers=HDR,
        files={"audio": ("clip.pcm", pcm, "application/octet-stream")},
        data={"format": "pcm_s16le", "target": "de", "sample_rate": "16000"},
    )
    assert ok.status_code == 200
    bad = c.post(
        "/api/sdk/v1/translate/audio",
        headers=HDR,
        files={"audio": ("clip.mp3", b"ID3xxxx", "audio/mpeg")},
        data={"format": "mp3", "target": "de"},
    )
    assert bad.status_code == 415
    assert bad.json()["error"]["code"] == "unsupported_format"


def test_sdk_no_speech(make_client):
    c = make_client(env=ENV)
    c.stt.push("", "en")
    r = c.post(
        "/api/sdk/v1/translate/audio",
        headers=HDR,
        files={"audio": ("clip.wav", _wav(), "audio/wav")},
        data={"format": "wav", "target": "ru"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_speech"


def test_sdk_text_stream_sse(make_client):
    c = make_client(env=ENV)
    r = c.post("/api/sdk/v1/translate/text/stream", headers=HDR,
               json={"text": "hi", "source": "en", "target": "de"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: translation" in r.text
    assert "[en→de] hi" in r.text
    assert "event: done" in r.text


def test_token_bucket_first_token_at_rate_one():
    from talking.security import TokenBucket
    b = TokenBucket(1)
    assert b.allow("k") is True
    assert b.allow("k") is False


def test_sdk_rate_limit_per_key(make_client):
    c = make_client(env={**ENV, "SDK_RATE_LIMIT_PER_MIN": "1"})
    assert c.get("/api/sdk/v1/languages", headers=HDR).status_code == 200
    r = c.get("/api/sdk/v1/languages", headers=HDR)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers


def test_sdk_forced_offline(make_client):
    c = make_client(env=ENV)
    c.eng.set_force_offline(True)
    r = c.post("/api/sdk/v1/translate/text", headers=HDR,
               json={"text": "hello", "source": "en", "target": "ru"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "translation_unavailable"
