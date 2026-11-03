"""HTTP + WebSocket for press-and-hold voice translation.

  GET  /translate
  GET  /api/translate
  POST /api/translate/record
  WS   /ws/translate/live
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from talking.auth import authenticate_websocket
from talking.auth.capabilities import deny_anonymous_capability, effective_limits, policy_of
from talking.auth.deps import require_user
from talking.chat.media import MediaError, parse_waveform, sniff_container, validate_duration_ms
from talking.chat.models import Message
from talking.chat.notes import decode_pcm16k
from talking.ids import check_id
from talking.languages import WHISPER_LANGUAGES
from talking.security import TokenBucket, client_ip
from talking.stt import STTUnavailable
from talking.vad import pcm16_to_float

from .entitlement import live_translate_allowed
from .pipeline import normalize_lang, transcribe_and_translate
from .session import LiveTranslateSession

log = logging.getLogger(__name__)


def _user_id(request: Request) -> str:
    deny_anonymous_capability(request, "voice_translate")
    return require_user(request).id


def _public_message(app: FastAPI, user_id: str, msg: Message) -> dict:
    play = None
    if msg.media_id and getattr(app.state, "chat_tokens", None):
        tok = app.state.chat_tokens.mint(
            msg.media_id, user_id, one_time=False, ttl=app.state.settings.chat_play_token_ttl,
        )
        play = f"/api/chat/media/{msg.media_id}?token={tok}"
    return msg.to_public(play_url=play)


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    n = 0
    while True:
        piece = await upload.read(64 * 1024)
        if not piece:
            break
        n += len(piece)
        if n > limit:
            raise HTTPException(413, "file too large")
        chunks.append(piece)
    return b"".join(chunks)


def _attach_voice_note(app: FastAPI, uid: str, dialog_id: str, blob: bytes,
                       duration_ms: int, waveform, result, *, quota_bytes: int | None = None) -> dict:
    store = getattr(app.state, "chat_store", None)
    media = getattr(app.state, "chat_media", None)
    hub = getattr(app.state, "chat_hub", None)
    if store is None or media is None:
        raise HTTPException(503, "chat notes slice is not available")
    try:
        store.get_dialog(uid, dialog_id)
    except (ValueError, KeyError) as e:
        raise HTTPException(404, str(e) or "dialog not found")
    try:
        stored = media.put(uid, "voice_note", blob, filename="translate.wav", quota_bytes=quota_bytes)
    except MediaError as e:
        raise HTTPException(e.status, str(e))
    msg = Message(
        id="m" + uuid.uuid4().hex[:15],
        dialog_id=dialog_id,
        sender_id=uid,
        type="voice_note",
        created_at=time.time(),
        duration_ms=duration_ms,
        media_id=stored.id,
        mime=stored.mime,
        size=stored.size,
        waveform=waveform,
        transcript=result.text,
        translation=result.translation,
        src_lang=result.src_lang,
        tgt_lang=result.tgt_lang,
        translation_provider=result.provider,
        transcript_status="ok" if result.text else "empty",
        extra={"source": "voice_translate"},
    )
    store.add_message(uid, msg)
    public = _public_message(app, uid, msg)
    if hub is not None:
        hub.publish(uid, {"type": "chat.message", "message": public})
    return public


def register_voice_translate(app: FastAPI) -> None:
    s = app.state.settings
    record_limiter = TokenBucket(s.voice_translate_record_per_min, burst=s.voice_translate_record_per_min)
    live_limiter = TokenBucket(s.voice_translate_live_connects_per_min)
    anon_record_limiter = TokenBucket(
        max(1, s.anon_voice_translate_record_per_min or s.voice_translate_record_per_min),
        burst=max(1, s.anon_voice_translate_record_per_min or s.voice_translate_record_per_min),
    )
    app.state.voice_translate_record_limiter = record_limiter
    app.state.voice_translate_live_limiter = live_limiter
    app.state.anon_voice_translate_record_limiter = anon_record_limiter

    def _session_factory(engine, emit, user_id: str) -> LiveTranslateSession:
        factory = getattr(app.state, "live_session_factory", None)
        if factory is not None:
            return factory(engine, emit, user_id)
        return LiveTranslateSession(engine, emit, user_id)

    @app.get("/translate", response_class=HTMLResponse)
    def translate_page():
        from talking.server import _html
        return _html("translate.html", s)

    @app.get("/api/translate")
    def translate_status(request: Request):
        uid = _user_id(request)
        eng = app.state.engine
        entitlement = live_translate_allowed(eng, uid)
        return {
            "ok": True,
            "module": "voice_translate",
            "modes": ["live", "record"],
            "live_premium": entitlement["premium_gated"],
            "live_allowed": entitlement["allowed"],
            "live_reason": entitlement["reason"],
            "live_max_s": s.voice_translate_live_max_s,
            "record_max_s": s.voice_translate_record_max_ms / 1000.0,
            "partial_interval_s": s.voice_translate_partial_interval_s,
            "stt": "faster-whisper",
            "streaming": "vad_windows",
            "providers": [p.name for p in eng.translator.active_providers()],
            "privacy": (
                "Live PCM is kept only for the current utterance and discarded on "
                "eou/stop. Record audio is persisted only when dialog_id is set "
                "(chat voice_note); otherwise it is decoded in memory and dropped."
            ),
            "entitlement": entitlement,
        }

    @app.post("/api/translate/record")
    async def record_translate(
        request: Request,
        file: UploadFile = File(...),
        source_lang: str = Form("auto"),
        target_lang: str = Form("en"),
        partner_lang: str = Form(""),
        tts: str = Form("0"),
        dialog_id: str = Form(""),
        duration_ms: int = Form(800),
        waveform: str = Form(""),
    ):
        uid = _user_id(request)
        limiter = (
            anon_record_limiter
            if policy_of(request).applies_to(getattr(request.state, "user", None))
            else record_limiter
        )
        if not limiter.allow(uid) or not limiter.allow("ip:" + client_ip(request)):
            raise HTTPException(429, "rate limit: too many record translations")
        src = normalize_lang(source_lang, allow_auto=True) or "auto"
        tgt = normalize_lang(target_lang)
        if not tgt:
            raise HTTPException(400, "unknown target_lang")
        partner = normalize_lang(partner_lang) or (s.lang_a if s.lang_a != tgt else "en")
        try:
            dur = validate_duration_ms(duration_ms, s.voice_translate_record_max_ms)
        except MediaError as e:
            raise HTTPException(e.status if e.status else 400, str(e))
        peaks = None
        if waveform:
            try:
                peaks = parse_waveform(waveform)
            except MediaError as e:
                raise HTTPException(400, str(e))
        blob = await _read_limited(file, s.chat_voice_max_bytes)
        container = sniff_container(blob)
        if container is None:
            raise HTTPException(400, "unsupported audio (need wav/webm/ogg/mp4/mp3)")
        audio = decode_pcm16k(blob, container, s.sample_rate)
        if audio is None or len(audio) < 160:
            raise HTTPException(
                422,
                f"no PCM decoder for {container} "
                "(WAV works without ffmpeg; WebM/MP4/OGG need ffmpeg on PATH)",
            )
        try:
            result = transcribe_and_translate(
                app.state.engine, audio,
                source_lang=src, target_lang=tgt, partner_lang=partner, load_stt=True,
            )
        except STTUnavailable as e:
            raise HTTPException(503, str(e))
        message = None
        audio_retained = False
        did = (dialog_id or "").strip()
        if did:
            quota = int(effective_limits(request).get("chat_quota_bytes") or s.chat_quota_bytes)
            message = _attach_voice_note(
                app, uid, did, blob, dur, peaks, result, quota_bytes=quota,
            )
            audio_retained = True
        want_tts = str(tts).strip().lower() in ("1", "true", "yes", "on")
        body = {
            "ok": True,
            "mode": "record",
            **result.to_public(),
            "duration_ms": dur,
            "tts": want_tts,
            "audio_retained": audio_retained,
            "message": message,
            "speak": (
                {"lang": result.tgt_lang, "text": result.translation}
                if want_tts and result.translation else None
            ),
        }
        return JSONResponse(body, status_code=201 if message else 200)

    @app.websocket("/ws/translate/live")
    async def ws_live(ws: WebSocket, t: str = ""):
        eng = app.state.engine
        ip = client_ip(ws)
        if not live_limiter.allow(ip):
            await ws.close(code=1008, reason="rate limit")
            return
        ctx = await authenticate_websocket(ws, t)
        if ctx is None or not ctx.authenticated:
            await ws.close(code=1008, reason="handshake")
            return
        if ctx.auth_mode == "accounts" and ctx.is_anonymous:
            policy = getattr(ws.app.state, "anon_policy", None)
            if policy is not None and not policy.allows("voice_translate"):
                await ws.close(code=1008, reason="capability_denied")
                return
            ws_lim = getattr(ws.app.state, "anon_ws_limiter", None)
            if ws_lim is not None and not ws_lim.allow(ip):
                await ws.close(code=1008, reason="rate limit")
                return
        uid = ctx.id
        try:
            uid = check_id(uid)
        except ValueError:
            await ws.close(code=1008, reason="handshake")
            return
        entitlement = live_translate_allowed(eng, uid)
        if not entitlement["allowed"]:
            await ws.accept()
            await ws.send_text(json.dumps({
                "type": "error",
                "code": "not_entitled",
                "reason": entitlement["reason"],
                "premium_gated": True,
            }, ensure_ascii=False))
            await ws.close(code=1008, reason="not entitled")
            return
        await ws.accept()
        app.state.auth_sockets.add(ws, uid, ctx.session_id)
        loop = asyncio.get_running_loop()
        maxsize = max(8, int(s.voice_translate_max_queue))
        out: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

        def emit(ev: dict):
            def _put():
                if out.full():
                    if ev.get("type") == "partial":
                        return
                    try:
                        out.get_nowait()
                    except Exception:
                        return
                try:
                    out.put_nowait(ev)
                except Exception:
                    pass
            loop.call_soon_threadsafe(_put)

        sess = _session_factory(eng, emit, uid)
        msg_limiter = TokenBucket(60 * 120, burst=400)
        ctl_limiter = TokenBucket(600, burst=60)

        async def sender():
            while True:
                ev = await out.get()
                await ws.send_text(json.dumps(ev, ensure_ascii=False))

        send_task = asyncio.create_task(sender())
        emit(sess.state())
        emit({"type": "entitlement", **entitlement})
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if not msg_limiter.allow("a") or len(msg["bytes"]) > 64000:
                        continue
                    if sess.started:
                        sess.feed_pcm(pcm16_to_float(msg["bytes"]))
                    continue
                if not ctl_limiter.allow("c"):
                    emit({"type": "notice", "level": "warning", "text": "Too many commands"})
                    continue
                txt = msg.get("text") or "{}"
                if len(txt) > 20000:
                    continue
                try:
                    data = json.loads(txt)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                tp = data.get("type")
                if tp == "config":
                    kw = {k: data[k] for k in (
                        "source_lang", "target_lang", "partner_lang", "tts", "client_vad",
                    ) if k in data}
                    emit(sess.configure(**kw))
                elif tp == "start":
                    emit(sess.start())
                elif tp == "eou":
                    sess.end_of_utterance()
                elif tp == "stop":
                    emit(sess.stop())
                elif tp == "swap":
                    emit(sess.swap())
                elif tp == "ping":
                    emit({"type": "pong", "t": data.get("t") if isinstance(data.get("t"), (int, float)) else None})
        except WebSocketDisconnect:
            pass
        finally:
            app.state.auth_sockets.discard(ws)
            sess.close()
            send_task.cancel()
