"""HTTP + WebSocket endpoints for notes + M4 1:1 chat.

Designed for the M4 direct-chat and M6 group extensions.
  GET  /api/chat
  GET/PATCH /api/chat/me
  GET  /api/chat/users
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

from talking.commercial.client import ENTITLEMENT_VOICE_TRANSCRIPTION
from talking.ids import check_id
from talking.identity import handshake_websocket, request_user_id
from talking.languages import WHISPER_LANGUAGES
from talking.security import TokenBucket, client_ip

from . import PARTIAL
from .directory import ChatDirectory, NickError, REPORT_REASONS
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
from .nicks import SEARCH_LIMIT
from .notes import attach_transcript
from .store import ChatStore
from .transcribe import TranscriptionService
from .translate_msg import src_lang_for
from .views import dialog_public, shape_message, viewer_context

log = logging.getLogger(__name__)
TEXT_MAX = 500


def _http(
    err: MediaError | ValueError | KeyError | GroupError | PermissionError | NickError
) -> HTTPException:
    if isinstance(err, MediaError):
        return HTTPException(err.status, str(err))
    if isinstance(err, (GroupError, NickError)):
        return HTTPException(err.status, getattr(err, "message", str(err)))
    if isinstance(err, PermissionError):
        return HTTPException(403, str(err) or "forbidden")
    if isinstance(err, KeyError):
        return HTTPException(404, str(err) or "not found")
    return HTTPException(400, str(err))


def _user_id(request: Request) -> str:
    return request_user_id(request)


def _resolve_peer(directory: ChatDirectory, data: dict) -> str:
    if data.get("user_id"):
        return check_id(str(data["user_id"]), "user_id")
    if data.get("nickname"):
        found = directory.find_nick(str(data["nickname"]))
        if found is None:
            raise NickError("user not found", status=404)
        return found[0]
    raise NickError("user_id or nickname required")


def _play_url(app: FastAPI, msg: Message) -> str | None:
    if not msg.media_id:
        return None
    owner = msg.sender_id
    tok = app.state.chat_tokens.mint(
        msg.media_id, owner, one_time=False, ttl=app.state.settings.chat_play_token_ttl,
    )
    return f"/api/chat/media/{msg.media_id}?token={tok}"


def _public_message(app: FastAPI, user_id: str, msg: Message, *, translate: bool = True) -> dict:
    store: ChatStore = app.state.chat_store
    if store.is_group(msg.dialog_id):
        return public_message(app, user_id, msg)
    play = _play_url(app, msg)
    directory: ChatDirectory | None = getattr(app.state, "chat_directory", None)
    if directory is None or not translate:
        return msg.to_public(play_url=play, viewer_id=user_id)
    auto, tgt, _peer = viewer_context(store, directory, user_id, msg.dialog_id)
    sender_native = directory.get_profile(msg.sender_id).native_lang
    cache = getattr(app.state, "chat_translations", None)
    translator = getattr(getattr(app.state, "engine", None), "translator", None)

    def _persist(updated: Message) -> None:
        store.update_message(user_id, updated)

    return shape_message(
        msg,
        viewer_id=user_id,
        play_url=play,
        auto=auto,
        tgt=tgt,
        src=src_lang_for(msg, sender_native),
        translator=translator,
        cache=cache,
        persist=_persist if translator is not None else None,
    )


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
    app.state.chat_directory = ChatDirectory(
        f"{data}/chat",
        secret=app.state.secret,
        rename_seconds=int(getattr(s, "chat_nick_rename_seconds", 0) or 0),
    )
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
    app.state.chat_search_limiter = TokenBucket(30, burst=30)
    app.state.chat_report_limiter = TokenBucket(5, burst=5)

    def _limit_msg(uid: str) -> None:
        if not app.state.chat_msg_limiter.allow(uid):
            raise HTTPException(429, "rate limit: 10 messages per minute")

    def _limit_upload(uid: str, ip: str) -> None:
        if not app.state.chat_upload_limiter.allow(uid) or not app.state.chat_upload_limiter.allow("ip:" + ip):
            raise HTTPException(429, "rate limit: too many uploads")

    def _public_for(uid: str, msg: Message) -> dict:
        return _public_message(app, uid, msg)

    register_group_routes(app)

    def _guard_direct(uid: str, dialog_id: str) -> None:
        store: ChatStore = app.state.chat_store
        directory: ChatDirectory = app.state.chat_directory
        if not store.is_direct(dialog_id):
            return
        peer = store.peer_id(uid, dialog_id)
        if peer and directory.is_blocked_pair(uid, peer):
            raise HTTPException(403, "blocked")

    def _fanout_message(msg: Message) -> None:
        store: ChatStore = app.state.chat_store
        hub: ChatHub = app.state.chat_hub
        if store.is_group(msg.dialog_id):
            publish_message(app, msg, actor_id=msg.sender_id)
            return
        try:
            parts = store.participants(msg.sender_id, msg.dialog_id)
        except (KeyError, ValueError):
            parts = [msg.sender_id]
        for part in parts:
            public = _public_message(app, part, msg)
            hub.publish(part, {"type": "chat.message", "dialog_id": msg.dialog_id, "message": public})
        peer = next((p for p in parts if p != msg.sender_id), None)
        if peer and hub.online(peer):
            store.mark_delivered(peer, msg.dialog_id, msg.id)
            store.apply_peer_receipt(msg.sender_id, msg.dialog_id, read=False)
            hub.publish(msg.sender_id, {
                "type": "chat.receipt", "dialog_id": msg.dialog_id,
                "status": "delivered", "user_id": peer, "last_id": msg.id,
            })

    def _fanout_deleted(uid: str, dialog_id: str, message_id: str) -> None:
        store: ChatStore = app.state.chat_store
        hub: ChatHub = app.state.chat_hub
        if store.is_group(dialog_id):
            publish_deleted(app, dialog_id, message_id, actor_id=uid)
            return
        try:
            parts = store.participants(uid, dialog_id)
        except (KeyError, ValueError):
            parts = [uid]
        hub.publish_users(parts, {"type": "chat.deleted", "dialog_id": dialog_id, "message_id": message_id})

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
            "core": True,
            "message": "Notes, M4 1:1 chat, and M6 groups.",
            "roadmap_m4": "Nicks, receipts, translation, and groups extend this store.",
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

    @app.get("/api/chat/me")
    def chat_me(request: Request):
        uid = _user_id(request)
        return app.state.chat_directory.get_profile(uid).to_public()

    @app.patch("/api/chat/me")
    async def patch_chat_me(request: Request):
        uid = _user_id(request)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        nick = data["nickname"] if "nickname" in data else None
        native = data.get("native_lang") if "native_lang" in data else None
        if native is not None and native != "" and native not in WHISPER_LANGUAGES:
            raise HTTPException(400, "unknown native_lang")
        auto = data.get("auto_translate") if "auto_translate" in data else None
        try:
            prof = app.state.chat_directory.set_profile(
                uid, nickname=nick, native_lang=native, auto_translate=auto,
            )
        except NickError as e:
            raise _http(e)
        return prof.to_public()

    @app.get("/api/chat/users")
    def search_users(request: Request, q: str = ""):
        uid = _user_id(request)
        if not app.state.chat_search_limiter.allow(uid):
            raise HTTPException(429, "rate limit")
        try:
            return {"ok": True, "users": app.state.chat_directory.search_prefix(q, viewer_id=uid, limit=SEARCH_LIMIT)}
        except NickError as e:
            raise _http(e)

    @app.get("/api/chat/dialogs")
    def list_dialogs(request: Request):
        uid = _user_id(request)
        directory: ChatDirectory = app.state.chat_directory
        store: ChatStore = app.state.chat_store
        profile = directory.get_profile(uid)
        return [dialog_public(store, directory, uid, d, profile=profile) for d in store.list_dialogs(uid)]

    @app.post("/api/chat/dialogs")
    async def create_dialog(request: Request):
        uid = _user_id(request)
        _limit_msg(uid)
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        data = data or {}
        directory: ChatDirectory = app.state.chat_directory
        store: ChatStore = app.state.chat_store
        try:
            if data.get("invite"):
                from_id = directory.consume_invite(str(data["invite"]), uid)
                if directory.is_blocked_pair(uid, from_id):
                    raise HTTPException(403, "blocked")
                d = store.open_direct(uid, from_id)
            elif data.get("nickname") or data.get("user_id"):
                if data.get("user_id"):
                    peer_id = check_id(str(data["user_id"]), "user_id")
                else:
                    found = directory.find_nick(str(data["nickname"]))
                    if found is None:
                        raise HTTPException(404, "user not found")
                    peer_id = found[0]
                if directory.is_blocked_pair(uid, peer_id):
                    raise HTTPException(403, "blocked")
                d = store.open_direct(uid, peer_id)
            else:
                title = str(data.get("title") or "Чат")
                d = store.create_dialog(uid, title)
        except (NickError, ValueError, KeyError) as e:
            raise _http(e)
        return dialog_public(store, directory, uid, d)

    @app.patch("/api/chat/dialogs/{dialog_id}")
    async def patch_dialog(request: Request, dialog_id: str):
        uid = _user_id(request)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        fields = {}
        if "auto_translate" in data:
            fields["auto_translate"] = bool(data["auto_translate"])
        if "target_lang" in data:
            tgt = data["target_lang"]
            if tgt in ("", None):
                fields["target_lang"] = None
            elif tgt not in WHISPER_LANGUAGES:
                raise HTTPException(400, "unknown target_lang")
            else:
                fields["target_lang"] = tgt
        try:
            d = app.state.chat_store.set_dialog_pref(uid, dialog_id, **fields)
        except (ValueError, KeyError) as e:
            raise _http(e)
        return dialog_public(app.state.chat_store, app.state.chat_directory, uid, d)

    @app.get("/api/chat/dialogs/{dialog_id}/messages")
    def list_messages(
        request: Request, dialog_id: str, limit: int = 100,
        before: str = "", after: str = "", after_ts: float = 0,
    ):
        uid = _user_id(request)
        _purge_retention(uid)
        try:
            msgs = app.state.chat_store.list_messages(
                uid, dialog_id,
                limit=min(max(limit, 1), 500),
                before=before or None,
                after=after or None,
                after_ts=after_ts or None,
            )
        except (ValueError, KeyError) as e:
            raise _http(e)
        body = [_public_message(app, uid, m) for m in msgs]
        has_more = False
        if msgs:
            has_more = app.state.chat_store.has_more_before(uid, dialog_id, msgs[0].id)
        return JSONResponse(body, headers={"X-Chat-Has-More": "1" if has_more else "0"})

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
            _guard_direct(uid, dialog_id)
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
        _fanout_message(msg)
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
            _guard_direct(uid, dialog_id)
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
            else:
                src = str(data.get("src_lang") or "") or None
                if not src:
                    src = app.state.chat_directory.get_profile(uid).native_lang or "en"
                if src in WHISPER_LANGUAGES:
                    msg.src_lang = src
            if tgt and not app.state.chat_store.is_group(dialog_id):
                try:
                    tr = app.state.engine.translator.translate(text, src or "en", tgt, always=True)
                    msg.src_lang = src or "en"
                    msg.tgt_lang = tgt
                    msg.transcript = text
                    msg.translation = tr.text
                    msg.translation_provider = tr.provider
                    msg.transcript_status = "ok"
                    msg.translations = {
                        tgt: {"text": tr.text, "src": src or "en", "tgt": tgt,
                              "provider": tr.provider, "failed": False},
                    }
                except Exception as e:  # noqa: BLE001
                    msg.transcript_status = "error"
                    msg.transcript_reason = str(e)[:200]
                    msg.translations = {
                        tgt: {"text": text, "src": src or "en", "tgt": tgt, "failed": True,
                              "reason": str(e)[:200]},
                    }
            app.state.chat_store.add_message(uid, msg)
            public = _public_message(app, uid, msg)
            _fanout_message(msg)
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
        _fanout_message(msg)
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
        if app.state.chat_store.is_direct(msg.dialog_id) and msg.sender_id != uid:
            raise HTTPException(403, "not the author")
        removed = app.state.chat_store.delete_message(uid, msg.dialog_id, msg.id)
        if removed and removed.media_id:
            app.state.chat_media.delete(removed.sender_id or uid, removed.media_id)
        _fanout_deleted(uid, msg.dialog_id, message_id)
        return {"ok": True, "id": message_id}

    @app.post("/api/chat/dialogs/{dialog_id}/read")
    async def mark_read(request: Request, dialog_id: str):
        uid = _user_id(request)
        try:
            data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        except json.JSONDecodeError:
            data = {}
        last_id = str((data or {}).get("last_id") or "") or None
        try:
            app.state.chat_store.get_dialog(uid, dialog_id)
            rec = app.state.chat_store.mark_read(uid, dialog_id, last_id)
            peer = app.state.chat_store.peer_id(uid, dialog_id)
        except (ValueError, KeyError) as e:
            raise _http(e)
        if peer:
            app.state.chat_store.apply_peer_receipt(peer, dialog_id, read=True)
            app.state.chat_hub.publish(peer, {
                "type": "chat.receipt", "dialog_id": dialog_id,
                "status": "read", "user_id": uid, "last_id": last_id,
            })
        return {"ok": True, "receipt": rec}

    @app.get("/api/chat/sync")
    def chat_sync(request: Request, after: float = 0):
        uid = _user_id(request)
        after = float(after or 0)
        store: ChatStore = app.state.chat_store
        msgs = [_public_message(app, uid, m) for m in store.messages_since(uid, after)]
        return {
            "ok": True,
            "server_time": time.time(),
            "after": after,
            "messages": msgs,
            "deleted": store.list_tombstones(uid, after_ts=after),
        }

    @app.post("/api/chat/invites")
    def create_invite(request: Request):
        uid = _user_id(request)
        ttl = int(getattr(s, "chat_invite_ttl_seconds", 7 * 24 * 3600) or 7 * 24 * 3600)
        return app.state.chat_directory.create_invite(uid, ttl=ttl)

    @app.get("/api/chat/invites/{token}")
    def peek_invite(request: Request, token: str):
        _user_id(request)
        try:
            return app.state.chat_directory.peek_invite(token)
        except NickError as e:
            raise _http(e)

    @app.post("/api/chat/invites/{token}/accept")
    def accept_invite(request: Request, token: str):
        uid = _user_id(request)
        directory: ChatDirectory = app.state.chat_directory
        try:
            from_id = directory.consume_invite(token, uid)
            if directory.is_blocked_pair(uid, from_id):
                raise HTTPException(403, "blocked")
            d = app.state.chat_store.open_direct(uid, from_id)
        except (NickError, ValueError, KeyError) as e:
            raise _http(e)
        return dialog_public(app.state.chat_store, directory, uid, d)

    @app.get("/api/chat/blocks")
    def list_blocks(request: Request):
        uid = _user_id(request)
        return {"ok": True, "blocks": app.state.chat_directory.list_blocks(uid)}

    @app.post("/api/chat/blocks")
    async def block_user(request: Request):
        uid = _user_id(request)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        directory: ChatDirectory = app.state.chat_directory
        try:
            peer_id = _resolve_peer(directory, data)
            directory.block(uid, peer_id)
        except (NickError, ValueError) as e:
            raise _http(e)
        return {"ok": True, "user": directory.public_peer(peer_id)}

    @app.delete("/api/chat/blocks/{peer_id}")
    def unblock_user(request: Request, peer_id: str):
        uid = _user_id(request)
        try:
            peer_id = check_id(peer_id, "user_id")
            app.state.chat_directory.unblock(uid, peer_id)
        except (NickError, ValueError) as e:
            raise _http(e)
        return {"ok": True, "id": peer_id}

    @app.post("/api/chat/reports")
    async def report_user(request: Request):
        uid = _user_id(request)
        if not app.state.chat_report_limiter.allow(uid):
            raise HTTPException(429, "rate limit")
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        directory: ChatDirectory = app.state.chat_directory
        try:
            peer_id = _resolve_peer(directory, data)
            reason = str(data.get("reason") or "other")
            if reason not in REPORT_REASONS:
                raise HTTPException(400, "reason must be spam, abuse, or other")
            # Drop any client-supplied message body — reports never store contents.
            out = directory.report(uid, peer_id, reason)
        except (NickError, ValueError) as e:
            raise _http(e)
        return out

    def _on_connect_deliver(uid: str) -> None:
        store: ChatStore = app.state.chat_store
        hub: ChatHub = app.state.chat_hub
        for d in store.list_dialogs(uid):
            if d.kind != "direct" or not d.last_message_id:
                continue
            store.mark_delivered(uid, d.id)
            peer = store.peer_id(uid, d.id)
            if peer:
                store.apply_peer_receipt(peer, d.id, read=False)
                hub.publish(peer, {
                    "type": "chat.receipt", "dialog_id": d.id,
                    "status": "delivered", "user_id": uid,
                })

    async def _ws_event(ws: WebSocket, uid: str, data: dict) -> None:
        kind = data.get("type")
        store: ChatStore = app.state.chat_store
        hub: ChatHub = app.state.chat_hub
        directory: ChatDirectory = app.state.chat_directory
        if kind == "ping":
            await ws.send_text(json.dumps({"type": "pong", "t": data.get("t")}))
            return
        if kind == "chat.typing":
            dialog_id = str(data.get("dialog_id") or "")
            try:
                peer = store.peer_id(uid, dialog_id)
            except (KeyError, ValueError):
                return
            if not peer or directory.is_blocked_pair(uid, peer):
                return
            hub.publish(peer, {
                "type": "chat.typing", "dialog_id": dialog_id,
                "user_id": uid, "on": bool(data.get("on", True)),
            })
            return
        if kind == "chat.read":
            dialog_id = str(data.get("dialog_id") or "")
            last_id = str(data.get("last_id") or "") or None
            try:
                store.get_dialog(uid, dialog_id)
                store.mark_read(uid, dialog_id, last_id)
                peer = store.peer_id(uid, dialog_id)
            except (KeyError, ValueError):
                return
            if peer:
                store.apply_peer_receipt(peer, dialog_id, read=True)
                hub.publish(peer, {
                    "type": "chat.receipt", "dialog_id": dialog_id,
                    "status": "read", "user_id": uid, "last_id": last_id,
                })
            return
        if kind == "chat.sync":
            after = float(data.get("after") or 0)
            msgs = [_public_message(app, uid, m) for m in store.messages_since(uid, after)]
            await ws.send_text(json.dumps({
                "type": "chat.sync", "server_time": time.time(), "after": after,
                "messages": msgs, "deleted": store.list_tombstones(uid, after_ts=after),
            }, ensure_ascii=False))
            return
        if kind == "chat.send":
            dialog_id = str(data.get("dialog_id") or "")
            text = str(data.get("text") or "").strip()
            if not text or len(text) > TEXT_MAX:
                await ws.send_text(json.dumps({"type": "chat.error", "error": "invalid text"}))
                return
            try:
                store.get_dialog(uid, dialog_id)
                _guard_direct(uid, dialog_id)
            except (KeyError, ValueError, HTTPException):
                await ws.send_text(json.dumps({"type": "chat.error", "error": "forbidden"}))
                return
            if not app.state.chat_msg_limiter.allow(uid):
                await ws.send_text(json.dumps({"type": "chat.error", "error": "rate limit"}))
                return
            src = directory.get_profile(uid).native_lang or "en"
            msg = Message(
                id="m" + uuid.uuid4().hex[:15],
                dialog_id=dialog_id, sender_id=uid, type="text",
                created_at=time.time(), text=text, src_lang=src,
            )
            if store.is_group(dialog_id):
                group = store.groups.get(dialog_id)
                src = viewer_lang(store, uid, dialog_id, src)
                fill_translations(app, msg, src_lang=src, members=group.members if group else [])
            store.add_message(uid, msg)
            _fanout_message(msg)
            return

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, t: str = ""):
        uid = await handshake_websocket(ws, t)
        if uid is None:
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        hub: ChatHub = app.state.chat_hub
        hub.subscribe(uid, q)

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
        await ws.send_text(json.dumps({
            "type": "chat.hello", "user_id": uid, "module": "chat",
            "server_time": time.time(),
        }))
        try:
            _on_connect_deliver(uid)
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
                if isinstance(data, dict):
                    await _ws_event(ws, uid, data)
        except WebSocketDisconnect:
            pass
        finally:
            app.state.auth_sockets.discard(ws)
            hub.unsubscribe(uid, q)
            send_task.cancel()
