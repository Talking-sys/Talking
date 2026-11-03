"""Python SDK public API: mocked HTTP + in-process FastAPI (ASGI)."""
from __future__ import annotations

import asyncio
import io
import logging
import wave

import httpx
import pytest

from talking_sdk import (
    AsyncTalkingClient,
    AuthError,
    ConfigError,
    OfflineError,
    RateLimitError,
    TalkingClient,
    TalkingError,
)
from talking.tts import TTSResult

KEY = "test-sdk-key-not-for-production"
ENV = {"SDK_API_KEYS": f"demo:{KEY}"}


class _TestClientTransport(httpx.BaseTransport):
    """Sync httpx transport over Starlette TestClient (ASGITransport is async-only)."""

    def __init__(self, test_client):
        self._c = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        headers = [(k, v) for k, v in request.headers.items() if k.lower() != "host"]
        body = request.read()
        r = self._c.request(request.method, path, headers=headers, content=body)
        return httpx.Response(r.status_code, headers=dict(r.headers), content=r.content, request=request)


def _wav(sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * sr)
    return buf.getvalue()


def test_client_requires_url_and_key():
    with pytest.raises(ConfigError):
        TalkingClient("", "k")
    with pytest.raises(ConfigError):
        TalkingClient("http://x", "")


def test_repr_and_logs_never_include_secret(caplog):
    caplog.set_level(logging.DEBUG, logger="talking_sdk")
    secret = "super-secret-sdk-key"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(200, json={"languages": [{"code": "en", "en": "English", "ru": "английский"}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cli = TalkingClient("http://game.example", secret, app_id="demo", http_client=http)
    assert secret not in repr(cli)
    cli.list_languages()
    joined = " ".join(r.message for r in caplog.records)
    assert secret not in joined
    assert secret not in repr(cli.list_languages()[0])


def test_mocked_translate_and_retries():
    n = {"v": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["v"] += 1
        if n["v"] == 1:
            return httpx.Response(
                429,
                json={"error": {"code": "rate_limited", "message": "slow", "retry_after": 0}},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json={
            "request_id": "r1", "source": "en", "target": "de", "text": "hi",
            "translation": "[en→de] hi", "provider": "stub", "elapsed_ms": 1,
        })

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cli = TalkingClient("http://game.example", "k", http_client=http, retry_backoff=0)
    cli.set_target_language("de")
    assert cli.get_target_language() == "de"
    tr = cli.translate_text("hi", source="en")
    assert tr.translation == "[en→de] hi"
    assert n["v"] == 2


def test_mocked_auth_error_no_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "nope"}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cli = TalkingClient("http://game.example", "bad", http_client=http, max_retries=3)
    with pytest.raises(AuthError) as ei:
        cli.list_languages()
    assert ei.value.status == 401


def test_offline_mode():
    from talking_sdk.models import Language

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network should not be used")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cli = TalkingClient("http://game.example", "k", http_client=http, offline=True)
    with pytest.raises(OfflineError):
        cli.translate_text("hi", target="de")
    with pytest.raises(OfflineError):
        cli.list_languages()
    cli._languages = [Language("en", "English", "английский", "en")]
    assert cli.list_languages()[0].code == "en"


def test_inprocess_fastapi_client(make_client):
    c = make_client(env=ENV)
    http = httpx.Client(transport=_TestClientTransport(c), base_url="http://test")
    with TalkingClient("http://test", KEY, app_id="demo", http_client=http) as cli:
        langs = cli.list_languages()
        assert any(x.code == "ru" for x in langs)
        cli.set_target_language("ru")
        tr = cli.translate_text("hello", source="en")
        assert tr.translation == "[en→ru] hello"
        assert tr.provider == "stub"
        c.stt.push("voice line", "en")
        audio_tr = cli.translate_audio(_wav(), format="wav", target="de")
        assert audio_tr.text == "voice line"
        assert audio_tr.translation == "[en→de] voice line"
        events = list(cli.translate_text_stream("bye", source="en", target="fr"))
        kinds = [e.event for e in events]
        assert "translation" in kinds and "done" in kinds
        final = next(e.translation for e in events if e.translation)
        assert final.translation == "[en→fr] bye"
        info = cli.info()
        assert info["api"] == "talking-sdk"


def test_inprocess_async_client(make_client):
    c = make_client(env=ENV)

    async def run():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app_), base_url="http://test")
        async with AsyncTalkingClient("http://test", KEY, app_id="demo", http_client=http) as cli:
            cli.set_target_language("es")
            tr = await cli.translate_text("hello", source="en")
            assert tr.translation == "[en→es] hello"
            langs = await cli.list_languages()
            assert len(langs) == len(c.get("/api/languages").json())

    asyncio.run(run())


def test_inprocess_return_audio_bytes(make_client):
    class FakeTTS:
        last_errors: list[str] = []

        def synth(self, text, lang):
            return TTSResult(b"RIFF-fake", "audio/wav", "fake", 2)

    c = make_client(env=ENV, tts=FakeTTS())
    http = httpx.Client(transport=_TestClientTransport(c), base_url="http://test")
    cli = TalkingClient("http://test", KEY, http_client=http)
    tr = cli.translate_text("hi", source="en", target="de", return_audio=True)
    assert tr.audio == b"RIFF-fake"
    assert tr.audio_mime == "audio/wav"
    cli.close()


def test_rate_limit_error_maps(make_client):
    c = make_client(env={**ENV, "SDK_RATE_LIMIT_PER_MIN": "1"})
    http = httpx.Client(transport=_TestClientTransport(c), base_url="http://test")
    cli = TalkingClient("http://test", KEY, http_client=http, max_retries=0)
    cli.list_languages()
    with pytest.raises(RateLimitError):
        cli.list_languages(refresh=True)
    cli.close()


def test_talking_error_repr_has_no_key():
    err = TalkingError("nope", code="unauthorized", status=401)
    assert "Bearer" not in repr(err)
    assert err.code == "unauthorized"
