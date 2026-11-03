"""HTTP + WebSocket endpoints for the notes slice.

Designed so M4 can keep these paths and add nicks beside them. Groups (M6):
  GET  /api/chat
  GET/POST /api/chat/dialogs
  GET/POST /api/chat/dialogs/{id}/messages
  POST /api/chat/upload
  POST /api/chat/dialogs/{id}/notes
  POST /api/chat/messages/{id}/transcribe
  GET  /api/chat/messages/{id}/transcript
  DELETE /api/chat/messages/{id}
  GET  /api/chat/media/{id}
  POST /api/chat/media/{id}/token
  WS   /ws/chat
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from talking.auth import authenticate_websocket
from talking.auth.deps import require_user
from talking.commercial.client import ENTITLEMENT_VOICE_TRANSCRIPTION
from talking.ids import check_id
from talking.languages import WHISPER_LANGUAGES
from talking.security import TokenBucket, client_ip

from . import PARTIAL
from .events import ensure_hub
from .group_routes import register_group_routes
from .groups import GroupError
from .hub import ChatHub
from .present import (
    fill_translations,
    member_limit,
    public_message,
    publish_deleted,
    publish_message,
    viewer_lang,
)
from .media import (
    DownloadTokens, MediaError, MediaLibrary,
    check_media_id, parse_waveform, validate_duration_ms, validate_kind,
)
from .models import MESSAGE_TYPES, NOTE_TYPES, Message, transcript_view
from .notes import attach_transcript
from .store import ChatStore
from .transcribe import TranscriptionService

log = logging.getLogger(__name__)
TEXT_MAX = 500


def _http(err: MediaError | ValueError | KeyError | GroupError | PermissionError) -> HTTPException:
    if isinstance(err, MediaError):
        return HTTPException(err.status, str(err))
    if isinstance(err, GroupError):
        return HTTPException(err.status, err.message)
    if isinstance(err, PermissionError):
        return HTTPException(403, str(err) or "forbidden")
    if isinstance(err, KeyError):
        return HTTPException(404, str(err) or "not found")
    return HTTPException(400, str(err))


def _user_id(request: Request) -> str:
    return require_user(request).id


def _public_message(app: FastAPI, user_id: str, msg: Message) -> dict:
    return public_message(app, user_id, msg)


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


def register_chat(app: FastAPI) -> None:
    s = app.state.settings
    data = app.state.engine.s.data_dir
    app.state.chat_store = ChatStore(f"{data}/chat")
    app.state.chat_media = MediaLibrary(
        f"{data}/chat",
        quota_bytes=s.chat_quota_bytes,
        voice_max=s.chat_voice_max_bytes,
        video_max=s.chat_video_max_bytes,
    )
    app.state.chat_tokens = DownloadTokens(f"{data}/chat/download-tokens.json", app.state.secret)
    app.state.chat_hub = ensure_hub(app)
    app.state.chat_msg_limiter = TokenBucket(s.chat_msg_per_min, burst=s.chat_msg_per_min)
    app.state.chat_upload_limiter = TokenBucket(s.chat_upload_per_min, burst=s.chat_upload_per_min)
    app.state.chat_transcribe_limiter = TokenBucket(
        s.chat_transcribe_per_min, burst=s.chat_transcribe_per_min,
    )

    def _limit_msg(uid: str) -> None:
        if not app.state.chat_msg_limiter.allow(uid):
            raise HTTPException(429, "rate limit: 10 messages per minute")

    def _limit_upload(uid: str, ip: str) -> None:
        if not app.state.chat_upload_limiter.allow(uid) or not app.state.chat_upload_limiter.allow("ip:" + ip):
            raise HTTPException(429, "rate limit: too many uploads")

    def _public_for(uid: str, msg: Message) -> dict:
        return public_message(app, uid, msg)

    register_group_routes(app)

    app.state.transcription = TranscriptionService(
        engine=app.state.engine,
        store=app.state.chat_store,
        media=app.state.chat_media,
        hub=app.state.chat_hub,
        settings=s,
        public_message=_public_for,
    )

    def _purge_retention(uid: str) -> None:
        seconds = int(getattr(s, "chat_retention_seconds", 0) or 0)
        if seconds <= 0:
            return
        for old in app.state.chat_store.purge_older_than(uid, seconds):
            if old.media_id:
                app.state.chat_media.delete(uid, old.media_id)

    def _transcription_public(uid: str) -> dict:
        decision = app.state.engine.commercial.check_entitlement(
            uid, ENTITLEMENT_VOICE_TRANSCRIPTION,
        )
        free_mode = (s.chat_transcribe_free_mode or "lock").strip().lower()
        if free_mode not in ("none", "lock", "teaser", "hidden"):
            free_mode = "lock"
        return {
            "on_demand": True,
            "enabled": bool(s.chat_transcribe),
            "premium_required": True,
            "entitlement": ENTITLEMENT_VOICE_TRANSCRIPTION,
            "entitled": decision.entitled,
            "source": decision.source,
            "free_mode": free_mode,
            "provider": (s.chat_stt_provider or "faster-whisper"),
            "max_ms": s.chat_transcribe_max_ms,
            "rate_per_min": s.chat_transcribe_per_min,
        }

    def _premium_or_403(uid: str):
        decision = app.state.engine.commercial.check_entitlement(
            uid, ENTITLEMENT_VOICE_TRANSCRIPTION,
        )
        if decision.entitled:
            return decision
        hint = {
            "ru": "Расшифровка голосовых — функция Talking Premium.",
            "en": "Voice transcription is a Talking Premium feature.",
        }
        raise HTTPException(403, {
            "error": "premium_required",
            "entitlement": ENTITLEMENT_VOICE_TRANSCRIPTION,
            "source": decision.source,
            "hint": hint,
        })

    @app.get("/chat", response_class=HTMLResponse)
    def chat_page():
        from talking.server import _html
        return _html("chat.html", s)

    @app.get("/api/chat")
    def chat_status(request: Request):
        uid = _user_id(request)
        q = app.state.chat_media.quota(uid)
        return {
            "ok": True,
            "module": "chat",
            "stage": "notes-slice",
            "status": "partial",
            "partial": list(PARTIAL),
            "feature": "notes",
            "message": "Telegram-style voice_note / video_note. Full chat is ROADMAP M4.",
            "roadmap_m4": "Unique nicks, prefix search, block, 3-minute auto-delete extend this store.",
            "types": list(MESSAGE_TYPES),
            "limits": {
                "text_chars": TEXT_MAX,
                "note_max_ms": s.chat_note_max_ms,
                "voice_max_bytes": s.chat_voice_max_bytes,
                "video_max_bytes": s.chat_video_max_bytes,
                "quota_bytes": s.chat_quota_bytes,
                "messages_per_min": s.chat_msg_per_min,
                "group_max_members": member_limit(s, app.state.engine.commercial, uid),
                "group_trial_max_members": int(getattr(s, "chat_group_trial_max_members", 5) or 5),
            },
            "quota": q,
            "transcription": _transcription_public(uid),
        }

    @app.get("/api/chat/dialogs")
    def list_dialogs(request: Request):
        uid = _user_id(request)
        return [d.to_public() for d in app.state.chat_store.list_dialogs(uid)]

    @app.post("/api/chat/dialogs")
    async def create_dialog(request: Request):
        uid = _user_id(request)
        _limit_msg(uid)
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        title = str((data or {}).get("title") or "Чат")
        return app.state.chat_store.create_dialog(uid, title).to_public()

    @app.get("/api/chat/dialogs/{dialog_id}/messages")
    def list_messages(request: Request, dialog_id: str, limit: int = 100):
        uid = _user_id(request)
        _purge_retention(uid)
        try:
            msgs = app.state.chat_store.list_messages(uid, dialog_id, limit=min(max(limit, 1), 500))
        except (ValueError, KeyError) as e:
            raise _http(e)
        return [_public_message(app, uid, m) for m in msgs]

    @app.post("/api/chat/upload")
    async def upload_media(
        request: Request,
        file: UploadFile = File(...),
        kind: str = Form(...),
    ):
        uid = _user_id(request)
        _limit_upload(uid, client_ip(request))
        try:
            kind = validate_kind(kind)
            blob = await _read_limited(file, app.state.chat_media.max_for(kind))
            stored = app.state.chat_media.put(uid, kind, blob, filename=file.filename)
        except MediaError as e:
            raise _http(e)
        return stored.to_public()

    @app.post("/api/chat/media/{media_id}/token")
    async def mint_token(request: Request, media_id: str):
        uid = _user_id(request)
        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except json.JSONDecodeError:
                body = {}
        one_time = bool(body.get("one_time", True))
        ttl = int(body.get("ttl") or (
            s.chat_download_token_ttl if one_time else s.chat_play_token_ttl
        ))
        ttl = max(1, min(ttl, 3600))
        try:
            app.state.chat_media.get(uid, media_id)
        except MediaError as e:
            raise _http(e)
        token = app.state.chat_tokens.mint(media_id, uid, one_time=one_time, ttl=ttl)
        return {"token": token, "expires_in": ttl, "one_time": one_time,
                "url": f"/api/chat/media/{media_id}?token={token}"}

    @app.get("/api/chat/media/{media_id}")
    def get_media(request: Request, media_id: str, token: str = "", download: int = 0):
        uid = _user_id(request)
        if not token:
            raise HTTPException(401, "download token required")
        try:
            check_media_id(media_id)
            claims = app.state.chat_tokens.consume(token, media_id)
            owner = claims.get("u") or uid
            stored = app.state.chat_media.get(owner, media_id)
        except MediaError as e:
            raise _http(e)
        path = stored.path
        disp = "attachment" if download else "inline"
        ext = path.rsplit(".", 1)[-1] if "." in path else "bin"
        filename = f"{stored.kind}-{media_id[:8]}.{ext}"
        return FileResponse(
            path,
            media_type=stored.mime,
            filename=filename,
            headers={
                "Content-Disposition": f'{disp}; filename="{filename}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def _make_note_message(uid: str, dialog_id: str, stored, duration_ms: int,
                           waveform, data: bytes, target_lang: str | None) -> Message:
        msg = Message(
            id="m" + uuid.uuid4().hex[:15],
            dialog_id=dialog_id,
            sender_id=uid,
            type=stored.kind,
            created_at=time.time(),
            duration_ms=duration_ms,
            media_id=stored.id,
            mime=stored.mime,
            size=stored.size,
            waveform=waveform,
        )
        # On-demand by default. Optional legacy auto-STT is still premium-gated.
        if s.chat_transcribe_on_upload and s.chat_transcribe:
            if app.state.engine.commercial.has_entitlement(uid, ENTITLEMENT_VOICE_TRANSCRIPTION):
                attach_transcript(app.state.engine, msg, stored, data, target_lang)
        app.state.chat_store.add_message(uid, msg)
        return msg

    @app.post("/api/chat/dialogs/{dialog_id}/notes")
    async def post_note(
        request: Request,
        dialog_id: str,
        file: UploadFile = File(...),
        kind: str = Form(...),
        duration_ms: int = Form(...),
        waveform: str = Form(""),
        target_lang: str = Form(""),
    ):
        uid = _user_id(request)
        _limit_msg(uid)
        _limit_upload(uid, client_ip(request))
        try:
            app.state.chat_store.get_dialog(uid, dialog_id)
            kind = validate_kind(kind)
            dur = validate_duration_ms(duration_ms, s.chat_note_max_ms)
            peaks = parse_waveform(waveform) if waveform else None
            tgt = target_lang.strip() if target_lang else None
            if tgt and tgt not in WHISPER_LANGUAGES:
                raise HTTPException(400, "unknown target_lang")
            blob = await _read_limited(file, app.state.chat_media.max_for(kind))
            stored = app.state.chat_media.put(uid, kind, blob, filename=file.filename)
            msg = _make_note_message(uid, dialog_id, stored, dur, peaks, blob, tgt)
        except HTTPException:
            raise
        except (MediaError, ValueError, KeyError) as e:
            raise _http(e)
        public = _public_message(app, uid, msg)
        publish_message(app, msg, actor_id=uid)
        return JSONResponse(public, status_code=201)

    @app.post("/api/chat/dialogs/{dialog_id}/messages")
    async def post_message(request: Request, dialog_id: str):
        uid = _user_id(request)
        _limit_msg(uid)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        mtype = data.get("type")
        if mtype not in MESSAGE_TYPES:
            raise HTTPException(400, "type must be text, voice_note or video_note")
        try:
            app.state.chat_store.get_dialog(uid, dialog_id)
        except (ValueError, KeyError) as e:
            raise _http(e)
        tgt = data.get("target_lang") or None
        if tgt and tgt not in WHISPER_LANGUAGES:
            raise HTTPException(400, "unknown target_lang")

        if mtype == "text":
            text = str(data.get("text") or "").strip()
            if not text:
                raise HTTPException(400, "text required")
            if len(text) > TEXT_MAX:
                raise HTTPException(400, f"text exceeds {TEXT_MAX} characters")
            msg = Message(
                id="m" + uuid.uuid4().hex[:15],
                dialog_id=dialog_id,
                sender_id=uid,
                type="text",
                created_at=time.time(),
                text=text,
            )
            if app.state.chat_store.is_group(dialog_id):
                group = app.state.chat_store.groups.get(dialog_id)
                src = str(data.get("src_lang") or "") or viewer_lang(
                    app.state.chat_store, uid, dialog_id,
                    app.state.engine.store.get("target_lang") or "en",
                )
                fill_translations(app, msg, src_lang=src, members=group.members if group else [])
            elif tgt:
                src = str(data.get("src_lang") or "en")
                try:
                    tr = app.state.engine.translator.translate(text, src, tgt)
                    msg.src_lang = src
                    msg.tgt_lang = tgt
                    msg.transcript = text
                    msg.translation = tr.text
                    msg.translation_provider = tr.provider
                    msg.transcript_status = "ok"
                except Exception as e:  # noqa: BLE001
                    msg.transcript_status = "error"
                    msg.transcript_reason = str(e)[:200]
            app.state.chat_store.add_message(uid, msg)
            public = _public_message(app, uid, msg)
            publish_message(app, msg, actor_id=uid)
            return JSONResponse(public, status_code=201)

        # voice_note / video_note from a previously uploaded media_id
        try:
            stored = app.state.chat_media.get(uid, str(data.get("media_id") or ""))
            if stored.kind != mtype:
                raise HTTPException(400, "media kind does not match message type")
            dur = validate_duration_ms(data.get("duration_ms"), s.chat_note_max_ms)
            peaks = parse_waveform(data.get("waveform"))
            blob = app.state.chat_media.read_bytes(stored)
            msg = _make_note_message(uid, dialog_id, stored, dur, peaks, blob, tgt)
        except HTTPException:
            raise
        except (MediaError, ValueError, KeyError) as e:
            raise _http(e)
        public = _public_message(app, uid, msg)
        publish_message(app, msg, actor_id=uid)
        return JSONResponse(public, status_code=201)

    @app.post("/api/chat/messages/{message_id}/transcribe")
    async def transcribe_message(request: Request, message_id: str):
        uid = _user_id(request)
        _purge_retention(uid)
        if not s.chat_transcribe:
            raise HTTPException(503, "transcription disabled")
        try:
            message_id = check_id(message_id, "message_id")
        except ValueError:
            raise HTTPException(404, "message not found")
        msg = app.state.chat_store.find_message(uid, message_id)
        if msg is None:
            raise HTTPException(404, "message not found")
        if msg.type not in NOTE_TYPES:
            raise HTTPException(400, "not a voice or video note")
        cap = int(s.chat_transcribe_max_ms or s.chat_note_max_ms)
        if msg.duration_ms and msg.duration_ms > cap:
            raise HTTPException(400, f"note exceeds transcription cap of {cap} ms")
        svc: TranscriptionService = app.state.transcription
        if svc.cached_done(msg):
            return transcript_view(msg) | {"message": _public_message(app, uid, msg)}
        _premium_or_403(uid)
        if not app.state.chat_transcribe_limiter.allow(uid):
            raise HTTPException(429, "rate limit: too many transcriptions")
        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except json.JSONDecodeError:
                body = {}
        tgt = (body.get("target_lang") if isinstance(body, dict) else None) or None
        if tgt and tgt not in WHISPER_LANGUAGES:
            raise HTTPException(400, "unknown target_lang")
        updated = svc.request(uid, msg, target_lang=tgt)
        return transcript_view(updated) | {"message": _public_message(app, uid, updated)}

    @app.get("/api/chat/messages/{message_id}/transcript")
    def get_transcript(request: Request, message_id: str):
        uid = _user_id(request)
        _purge_retention(uid)
        try:
            message_id = check_id(message_id, "message_id")
        except ValueError:
            raise HTTPException(404, "message not found")
        msg = app.state.chat_store.find_message(uid, message_id)
        if msg is None:
            raise HTTPException(404, "message not found")
        if msg.type not in NOTE_TYPES:
            raise HTTPException(400, "not a voice or video note")
        return transcript_view(msg) | {"message": _public_message(app, uid, msg)}

    @app.delete("/api/chat/messages/{message_id}")
    def delete_message(request: Request, message_id: str):
        uid = _user_id(request)
        try:
            message_id = check_id(message_id, "message_id")
        except ValueError:
            raise HTTPException(404, "message not found")
        msg = app.state.chat_store.find_message(uid, message_id)
        if msg is None:
            raise HTTPException(404, "message not found")
        if app.state.chat_store.is_group(msg.dialog_id) and msg.sender_id != uid:
            group = app.state.chat_store.groups.get(msg.dialog_id)
            actor = group.member(uid) if group else None
            if actor is None or actor.role not in ("owner", "admin"):
                raise HTTPException(403, "not the author")
        removed = app.state.chat_store.delete_message(uid, msg.dialog_id, msg.id)
        if removed and removed.media_id:
            app.state.chat_media.delete(removed.sender_id or uid, removed.media_id)
        publish_deleted(app, msg.dialog_id, message_id, actor_id=uid)
        return {"ok": True, "id": message_id}

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, t: str = ""):
        ctx = await authenticate_websocket(ws, t)
        if ctx is None or not ctx.authenticated:
            await ws.close(code=1008, reason="handshake")
            return
        uid = ctx.id
        try:
            uid = check_id(uid)
        except ValueError:
            await ws.close(code=1008, reason="handshake")
            return
        await ws.accept()
        app.state.auth_sockets.add(ws, uid, ctx.session_id)
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        hub: ChatHub = app.state.chat_hub
        hub.subscribe(uid, q)
        hub.subscribe("*", q)

        def _event_visible(ev: dict) -> bool:
            if not isinstance(ev, dict):
                return True
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            dialog_id = (
                ev.get("group_id")
                or ev.get("dialog_id")
                or msg.get("dialog_id")
                or msg.get("conversation_id")
            )
            if ev.get("type") in (
                "chat.hello", "pong",
                "chat.group.kicked", "chat.group.left", "chat.group.dissolved",
            ):
                return True
            if dialog_id and app.state.chat_store.is_group(str(dialog_id)):
                return app.state.chat_store.groups.is_member(str(dialog_id), uid)
            return True

        async def sender():
            while True:
                ev = await q.get()
                if not _event_visible(ev):
                    continue
                await ws.send_text(json.dumps(ev, ensure_ascii=False))

        send_task = asyncio.create_task(sender())
        await ws.send_text(json.dumps({"type": "chat.hello", "user_id": uid, "module": "chat"}))
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                txt = msg.get("text") or ""
                if not txt:
                    continue
                try:
                    data = json.loads(txt)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong", "t": data.get("t")}))
        except WebSocketDisconnect:
            pass
        finally:
            app.state.auth_sockets.discard(ws)
            hub.unsubscribe(uid, q)
            hub.unsubscribe("*", q)
            send_task.cancel()
