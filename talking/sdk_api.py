"""Versioned HTTP API for the game-embed SDK (/api/sdk/v1).

Thin wrapper around the existing STT / translate / TTS pipeline. Game traffic
does not enter the live rolling buffer, chat, or per-user history — studios
send a line of dialogue or an audio clip and get a translation back.

Auth: API keys from SDK_API_KEYS (app_id:secret). Per-key token bucket.
Never log raw keys or utterance text.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import math
import uuid
import wave
from dataclasses import dataclass

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .languages import WHISPER_LANGUAGES, languages_payload
from .security import TokenBucket
from .stt import STTUnavailable
from .translate import TranslationUnavailable
from .tts import TTSResult

log = logging.getLogger(__name__)

SDK_VERSION = "v1"
SUPPORTED_AUDIO_FORMATS = ("wav", "wave", "pcm", "pcm_s16le", "s16le", "raw", "pcm_f32le", "f32le")


class SdkError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass
class SdkPrincipal:
    app_id: str
    key_id: str
    remaining: float
    limit: int


def parse_sdk_api_keys(raw: str) -> list[tuple[str, str]]:
    """Parse SDK_API_KEYS into (api_key, app_id) pairs. First ':' or '=' splits."""
    out: list[tuple[str, str]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        eq, col = part.find("="), part.find(":")
        if eq >= 0 and (col < 0 or eq < col):
            app_id, key = part.split("=", 1)
        elif col >= 0:
            app_id, key = part.split(":", 1)
        else:
            app_id, key = "default", part
        app_id, key = app_id.strip(), key.strip()
        if key:
            out.append((key, app_id or "default"))
    return out


def key_id_for(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _digest_eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a, b)
    except (TypeError, ValueError):
        return False


def match_api_key(pairs: list[tuple[str, str]], provided: str, claimed_app: str = "") -> str | None:
    """Constant-time-ish scan of configured keys. Returns app_id or None."""
    if not provided:
        return None
    matched: str | None = None
    for key, app_id in pairs:
        if _digest_eq(key, provided):
            matched = app_id
    if matched is None:
        return None
    if claimed_app and not _digest_eq(claimed_app, matched):
        return None
    return matched


def extract_api_key(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-talking-api-key") or "").strip()


def sdk_error_response(exc: SdkError) -> JSONResponse:
    body: dict = {"error": {"code": exc.code, "message": exc.message}}
    headers: dict[str, str] = {}
    if exc.retry_after is not None:
        body["error"]["retry_after"] = exc.retry_after
        headers["Retry-After"] = str(max(0, int(math.ceil(exc.retry_after))))
    return JSONResponse(body, status_code=exc.status, headers=headers)


def _rate_headers(principal: SdkPrincipal) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(principal.limit),
        "X-RateLimit-Remaining": str(max(0, int(principal.remaining))),
    }


def _json(principal: SdkPrincipal, body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=_rate_headers(principal))


def _resample(x: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or x.size == 0:
        return x.astype(np.float32, copy=False)
    n = int(round(len(x) * dst_rate / src_rate))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    fp = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(fp, xp, x.astype(np.float64)).astype(np.float32)


def decode_audio(data: bytes, fmt: str, sample_rate: int, target_rate: int = 16000) -> np.ndarray:
    fmt = (fmt or "wav").lower().lstrip(".")
    if not data:
        raise SdkError(400, "invalid_request", "audio is empty")
    if fmt in ("pcm", "pcm_s16le", "s16le", "raw"):
        if len(data) < 2:
            raise SdkError(400, "invalid_request", "audio is empty")
        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        return _resample(x, sample_rate or target_rate, target_rate)
    if fmt in ("pcm_f32le", "f32le"):
        x = np.frombuffer(data, dtype="<f4").astype(np.float32)
        return _resample(x, sample_rate or target_rate, target_rate)
    if fmt in ("wav", "wave"):
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                ch, sw, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
                raw = w.readframes(w.getnframes())
        except wave.Error as e:
            raise SdkError(400, "invalid_request", "invalid wav") from e
        if sw == 2:
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sw == 1:
            x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sw == 4:
            x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise SdkError(415, "unsupported_format", f"wav sample width {sw} is not supported")
        if ch > 1 and x.size:
            x = x.reshape(-1, ch).mean(axis=1)
        return _resample(x, rate, target_rate)
    raise SdkError(
        415, "unsupported_format",
        f"audio format '{fmt}' is not supported in v1 (wav, pcm_s16le, pcm_f32le)",
    )


def _check_lang(code: str, *, allow_auto: bool) -> str:
    c = (code or "").strip().lower()
    if allow_auto and c in ("", "auto"):
        return "auto"
    if c not in WHISPER_LANGUAGES:
        raise SdkError(400, "unsupported_language", f"unsupported language code: {code!r}")
    return c


def _pairs(request: Request) -> list[tuple[str, str]]:
    cached = getattr(request.app.state, "sdk_api_key_pairs", None)
    if cached is None:
        cached = parse_sdk_api_keys(request.app.state.settings.sdk_api_keys)
        request.app.state.sdk_api_key_pairs = cached
    return cached


def _limiter(request: Request) -> TokenBucket:
    lim = getattr(request.app.state, "sdk_limiter", None)
    if lim is None:
        lim = TokenBucket(request.app.state.settings.sdk_rate_limit_per_min)
        request.app.state.sdk_limiter = lim
    return lim


def authenticate(request: Request) -> SdkPrincipal:
    s = request.app.state.settings
    pairs = _pairs(request)
    if not pairs:
        raise SdkError(503, "sdk_disabled", "SDK API is disabled (SDK_API_KEYS is empty)")
    provided = extract_api_key(request)
    claimed = (request.headers.get("x-talking-app-id") or "").strip()
    app_id = match_api_key(pairs, provided, claimed)
    if app_id is None:
        raise SdkError(401, "unauthorized", "invalid or missing API key")
    kid = key_id_for(provided)
    bucket = _limiter(request)
    if not bucket.allow(kid):
        retry = 60.0 / max(1.0, float(s.sdk_rate_limit_per_min))
        raise SdkError(429, "rate_limited", "per-key rate limit exceeded", retry_after=retry)
    return SdkPrincipal(app_id=app_id, key_id=kid, remaining=bucket.remaining(kid),
                        limit=s.sdk_rate_limit_per_min)


def _validate_text(text: str, max_chars: int) -> str:
    if not isinstance(text, str):
        raise SdkError(400, "invalid_request", "text must be a string")
    text = text.strip()
    if not text:
        raise SdkError(400, "invalid_request", "text is empty")
    if len(text) > max_chars:
        raise SdkError(413, "payload_too_large", f"text exceeds {max_chars} characters")
    return text


async def _translate(request: Request, text: str, src: str, tgt: str):
    eng = request.app.state.engine
    s = request.app.state.settings
    if getattr(eng.translator, "force_offline", False):
        raise SdkError(503, "translation_unavailable", "translator is offline")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(eng.translator.translate, text, src, tgt),
            timeout=s.translate_timeout,
        )
    except asyncio.TimeoutError as e:
        raise SdkError(504, "timeout", "translation timed out") from e
    except TranslationUnavailable as e:
        raise SdkError(503, "translation_unavailable", "translation provider unavailable") from e


async def _transcribe(request: Request, audio: np.ndarray, language: str | None):
    eng = request.app.state.engine
    s = request.app.state.settings
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(eng.stt.transcribe, audio, language),
            timeout=max(s.translate_timeout, 30.0),
        )
    except asyncio.TimeoutError as e:
        raise SdkError(504, "timeout", "speech recognition timed out") from e
    except STTUnavailable as e:
        raise SdkError(503, "stt_unavailable", "speech recognition is unavailable") from e


async def _maybe_synth(request: Request, text: str, lang: str, want: bool) -> tuple[str | None, str | None, int | None, str | None]:
    if not want:
        return None, None, None, None
    eng = request.app.state.engine
    res = await asyncio.to_thread(eng.tts.synth, text, lang)
    if not isinstance(res, TTSResult) or not res.audio:
        why = "; ".join(getattr(eng.tts, "last_errors", []) or []) or "server TTS is not enabled"
        return None, None, None, why
    return base64.b64encode(res.audio).decode("ascii"), res.mime, res.elapsed_ms, None


def _payload(*, request_id: str, source: str, target: str, text: str, translation: str,
             provider: str, elapsed_ms: int, stt_ms: int | None = None,
             audio_b64: str | None = None, audio_mime: str | None = None,
             tts_ms: int | None = None, audio_error: str | None = None) -> dict:
    body = {
        "request_id": request_id,
        "source": source,
        "target": target,
        "text": text,
        "translation": translation,
        "provider": provider,
        "elapsed_ms": elapsed_ms,
        "stt_ms": stt_ms,
        "tts_ms": tts_ms,
        "audio": audio_b64,
        "audio_mime": audio_mime,
    }
    if audio_error:
        body["audio_error"] = audio_error
    return body


sdk_router = APIRouter(prefix="/api/sdk/v1", tags=["sdk"])


@sdk_router.get("")
@sdk_router.get("/")
async def sdk_info(request: Request):
    p = authenticate(request)
    s = request.app.state.settings
    eng = request.app.state.engine
    return _json(p, {
        "api": "talking-sdk",
        "version": SDK_VERSION,
        "server_version": request.app.version,
        "stt_ready": bool(getattr(eng.stt, "ready", False)),
        "tts_enabled": bool(s.tts_enabled and getattr(eng.tts, "engines", None)),
        "max_text_chars": s.sdk_max_text_chars,
        "max_audio_bytes": s.sdk_max_audio_bytes,
        "audio_formats": ["wav", "pcm_s16le", "pcm_f32le"],
        "stream": {"text": "POST /api/sdk/v1/translate/text/stream (SSE; one final event in v1)"},
    })


@sdk_router.get("/languages")
async def sdk_languages(request: Request):
    p = authenticate(request)
    langs = languages_payload()
    return _json(p, {
        "languages": langs,
        "count": len(langs),
        "source_auto": True,
        "note": "Codes are Whisper language ids used by the Talking open core.",
    })


@sdk_router.post("/translate/text")
async def sdk_translate_text(request: Request):
    p = authenticate(request)
    s = request.app.state.settings
    try:
        data = await request.json()
    except Exception as e:
        raise SdkError(400, "invalid_request", "invalid JSON") from e
    if not isinstance(data, dict):
        raise SdkError(400, "invalid_request", "JSON object required")
    text = _validate_text(str(data.get("text", "")), s.sdk_max_text_chars)
    src = _check_lang(str(data.get("source", "auto") or "auto"), allow_auto=True)
    tgt = _check_lang(str(data.get("target", "") or ""), allow_auto=False)
    want_audio = bool(data.get("return_audio"))
    rid = uuid.uuid4().hex[:12]
    log.info("sdk text app=%s key_id=%s src=%s tgt=%s chars=%d rid=%s",
             p.app_id, p.key_id, src, tgt, len(text), rid)
    tr = await _translate(request, text, src, tgt)
    audio_b64, mime, tts_ms, audio_err = await _maybe_synth(request, tr.text, tgt, want_audio)
    return _json(p, _payload(
        request_id=rid, source=src, target=tgt, text=text, translation=tr.text,
        provider=tr.provider, elapsed_ms=tr.elapsed_ms, audio_b64=audio_b64,
        audio_mime=mime, tts_ms=tts_ms, audio_error=audio_err,
    ))


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


@sdk_router.post("/translate/text/stream")
async def sdk_translate_text_stream(request: Request):
    """Streaming envelope (SSE). v1 emits status + one final translation event."""
    p = authenticate(request)
    s = request.app.state.settings
    try:
        data = await request.json()
    except Exception as e:
        raise SdkError(400, "invalid_request", "invalid JSON") from e
    if not isinstance(data, dict):
        raise SdkError(400, "invalid_request", "JSON object required")
    text = _validate_text(str(data.get("text", "")), s.sdk_max_text_chars)
    src = _check_lang(str(data.get("source", "auto") or "auto"), allow_auto=True)
    tgt = _check_lang(str(data.get("target", "") or ""), allow_auto=False)
    want_audio = bool(data.get("return_audio"))
    rid = uuid.uuid4().hex[:12]
    log.info("sdk text-stream app=%s key_id=%s src=%s tgt=%s chars=%d rid=%s",
             p.app_id, p.key_id, src, tgt, len(text), rid)

    async def gen():
        yield _sse("status", {"stage": "translating", "request_id": rid})
        try:
            tr = await _translate(request, text, src, tgt)
            audio_b64, mime, tts_ms, audio_err = await _maybe_synth(request, tr.text, tgt, want_audio)
            body = _payload(
                request_id=rid, source=src, target=tgt, text=text, translation=tr.text,
                provider=tr.provider, elapsed_ms=tr.elapsed_ms, audio_b64=audio_b64,
                audio_mime=mime, tts_ms=tts_ms, audio_error=audio_err,
            )
            yield _sse("translation", body)
            yield _sse("done", {"request_id": rid})
        except SdkError as e:
            yield _sse("error", {"code": e.code, "message": e.message, "request_id": rid})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        **_rate_headers(p),
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


async def _read_audio_body(request: Request) -> tuple[bytes, str, str, str, bool, int]:
    s = request.app.state.settings
    ctype = (request.headers.get("content-type") or "").lower()
    max_b = s.sdk_max_audio_bytes
    if "application/json" in ctype:
        try:
            data = await request.json()
        except Exception as e:
            raise SdkError(400, "invalid_request", "invalid JSON") from e
        if not isinstance(data, dict):
            raise SdkError(400, "invalid_request", "JSON object required")
        b64 = data.get("audio_base64") or data.get("audio") or ""
        if not isinstance(b64, str) or not b64:
            raise SdkError(400, "invalid_request", "audio_base64 is required")
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as e:
            raise SdkError(400, "invalid_request", "audio_base64 is not valid base64") from e
        if len(raw) > max_b:
            raise SdkError(413, "payload_too_large", f"audio exceeds {max_b} bytes")
        fmt = str(data.get("format") or "wav")
        src = str(data.get("source") or "auto")
        tgt = str(data.get("target") or "")
        want = bool(data.get("return_audio"))
        sr = int(data.get("sample_rate") or 16000)
        return raw, fmt, src, tgt, want, sr

    form = await request.form()
    upload = form.get("audio")
    raw = b""
    if upload is None and form.get("audio_base64"):
        try:
            raw = base64.b64decode(str(form.get("audio_base64")), validate=False)
        except Exception as e:
            raise SdkError(400, "invalid_request", "audio_base64 is not valid base64") from e
    elif upload is None:
        raise SdkError(400, "invalid_request", "audio file is required")
    else:
        read = getattr(upload, "read", None)
        if read is None:
            raise SdkError(400, "invalid_request", "audio file is required")
        raw = await read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
    if len(raw) > max_b:
        raise SdkError(413, "payload_too_large", f"audio exceeds {max_b} bytes")
    fmt = str(form.get("format") or getattr(upload, "filename", "") or "wav")
    if "." in fmt and "/" not in fmt:
        fmt = fmt.rsplit(".", 1)[-1]
    src = str(form.get("source") or "auto")
    tgt = str(form.get("target") or "")
    want_raw = form.get("return_audio", "false")
    want = str(want_raw).strip().lower() in ("1", "true", "yes", "on")
    try:
        sr = int(form.get("sample_rate") or 16000)
    except (TypeError, ValueError):
        sr = 16000
    return raw, fmt, src, tgt, want, sr


@sdk_router.post("/translate/audio")
async def sdk_translate_audio(request: Request):
    p = authenticate(request)
    s = request.app.state.settings
    raw, fmt, src_in, tgt_in, want_audio, sample_rate = await _read_audio_body(request)
    src_hint = _check_lang(src_in, allow_auto=True)
    tgt = _check_lang(tgt_in, allow_auto=False)
    if sample_rate <= 0 or sample_rate > 96000:
        raise SdkError(400, "invalid_request", "sample_rate out of range")
    samples = decode_audio(raw, fmt, sample_rate, target_rate=s.sample_rate)
    rid = uuid.uuid4().hex[:12]
    log.info("sdk audio app=%s key_id=%s fmt=%s bytes=%d tgt=%s rid=%s",
             p.app_id, p.key_id, fmt, len(raw), tgt, rid)
    lang = None if src_hint == "auto" else src_hint
    stt = await _transcribe(request, samples, lang)
    if not stt.text:
        raise SdkError(422, "no_speech", "no speech recognised in audio")
    src = stt.lang if src_hint == "auto" else src_hint
    if src not in WHISPER_LANGUAGES:
        src = src if src == "auto" else _check_lang(src, allow_auto=True)
    tr = await _translate(request, stt.text, src, tgt)
    audio_b64, mime, tts_ms, audio_err = await _maybe_synth(request, tr.text, tgt, want_audio)
    return _json(p, _payload(
        request_id=rid, source=src, target=tgt, text=stt.text, translation=tr.text,
        provider=tr.provider, elapsed_ms=tr.elapsed_ms, stt_ms=stt.elapsed_ms,
        audio_b64=audio_b64, audio_mime=mime, tts_ms=tts_ms, audio_error=audio_err,
    ) | {"lang_prob": round(float(stt.lang_prob or 0.0), 3)})
