"""FastAPI HTTP + WebSocket server for the Talking open core."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .chat import not_implemented as chat_stub
from .chat.events import ensure_hub, register_chat_ws
from .chat.http import register_message_routes
from .chat.messages import MessageStore
from .chat.translation_cache import MessageTranslationCache
from .commercial.client import verify_webhook
from .config import Settings, WHISPER_MODELS
from .engine import Engine
from .games import not_implemented as games_stub
from .ids import secure_dir
from .languages import languages_payload
from .security import SecurityHeadersMiddleware, TokenBucket, client_ip, load_server_secret, sign, unsign
from .settings_store import PAUSE_MAX, PAUSE_MIN
from .store import ping_postgres, ping_redis
from .unfurl.routes import register_unfurl_routes
from .unfurl.service import UnfurlService
from .vad import pcm16_to_float

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


def _html(name: str, settings: Settings) -> HTMLResponse:
    html = (STATIC / name).read_text(encoding="utf-8")
    html = html.replace("{{APP_NAME}}", settings.app_name)
    html = html.replace("{{VERSION}}", __version__)
    html = html.replace("{{CONTACT}}", settings.contact_email)
    return HTMLResponse(html)


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               unfurl: UnfurlService | None = None,
               messages: MessageStore | None = None) -> FastAPI:
    s = settings or Settings()
    secure_dir(s.data_dir)
    eng = engine or Engine(s)
    secret = load_server_secret(s.data_dir, s.secret_key)
    app = FastAPI(title=s.app_name, version=__version__,
                  docs_url="/docs" if not s.production else None,
                  redoc_url=None)
    app.state.engine = eng
    app.state.settings = s
    app.state.secret = secret
    # Link previews: isolated service + in-memory messages (M4 will replace the store).
    unfurl = unfurl or UnfurlService(
        redis_url=s.redis_url,
        stub=s.unfurl_stub,
        cache_ttl=s.unfurl_cache_ttl,
        timeout=s.unfurl_timeout,
        max_html_bytes=s.unfurl_max_bytes,
        max_redirects=s.unfurl_max_redirects,
    )
    messages = messages or MessageStore()
    app.state.unfurl = unfurl
    app.state.messages = messages
    app.state.chat_translations = MessageTranslationCache()
    ensure_hub(app)

    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PATCH"],
                           allow_headers=["Content-Type"], allow_credentials=True, max_age=600)
    app.add_middleware(SecurityHeadersMiddleware, production=s.production)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    ws_limiter = TokenBucket(s.ws_connects_per_min)
    api_limiter = TokenBucket(s.rate_limit_per_min)
    unfurl_limiter = TokenBucket(s.unfurl_rate_per_min)
    chat_msg_limiter = TokenBucket(s.chat_msg_rate_per_min)
    chat_edit_limiter = TokenBucket(s.chat_edit_rate_per_min)
    app.state.chat_edit_limiter = chat_edit_limiter

    def _limit(request: Request):
        if not api_limiter.allow(client_ip(request)):
            raise HTTPException(429, "rate limit")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _html("index.html", s)

    @app.get("/favicon.ico")
    def favicon():
        from fastapi.responses import FileResponse
        return FileResponse(STATIC / "logo.svg", media_type="image/svg+xml")

    @app.get("/healthz")
    async def healthz():
        pg = await ping_postgres(s.database_url)
        rd = ping_redis(s.redis_url)
        body = {
            "ok": True,
            "version": __version__,
            "app": s.app_name,
            "postgres": pg.__dict__,
            "redis": rd.__dict__,
            "stt_ready": eng.stt.ready,
            "commercial": eng.commercial.status()["mode"],
        }
        # Optional deps must not fail local/no-key runs. If they ARE configured, surface failure.
        if (pg.configured and not pg.ok) or (rd.configured and not rd.ok):
            return JSONResponse(body | {"ok": False}, status_code=503)
        return body

    @app.get("/readyz")
    async def readyz():
        return await healthz()

    @app.get("/.well-known/security.txt", response_class=PlainTextResponse)
    def security_txt():
        return (f"Contact: mailto:{s.contact_email}\nPreferred-Languages: ru, en\n"
                "Policy: /privacy\n")

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /api/\n"

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Privacy</title>"
            "<body style='font-family:system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem'>"
            f"<h1>Privacy (template)</h1><p>Speech segments are processed on Talking servers to "
            "provide translation. Recognition runs locally (Whisper). Translated text is sent to "
            "third-party APIs (xAI and others) as segment text without an account binding. Cloud "
            "translation can be turned off, but the feature then becomes unavailable.</p>"
            f"<p>Contact: {s.contact_email}. This page is a lawyer-ready template, not legal advice.</p>"
            "<h2>Link previews</h2>"
            "<p>When a message contains a URL, Talking's servers fetch publicly available "
            "metadata (Open Graph, Twitter Card, oEmbed, HTML title) to show a preview card. "
            "The destination site sees Talking's server IP, not yours. Cookies are never sent. "
            "Preview images are downloaded, re-encoded (EXIF stripped) and served from Talking "
            "so other users' devices do not hotlink the origin. Opening a link uses a sandboxed "
            "iframe (no javascript: or data: URLs) or falls back to the system browser.</p>"
            "</body>"
        )

    @app.get("/terms", response_class=HTMLResponse)
    def terms():
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Terms</title>"
            "<body style='font-family:system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem'>"
            "<h1>Terms (template)</h1><p>You pay for access to the hosted service and to proprietary "
            "modules (admin, licensing, Talk-coins, P2P payments, analytics) distributed separately "
            "under an All-rights-reserved licence. The open core is GPL v3.</p>"
            f"<p>Copyright holder: hateaboutyouone. Contact: {s.contact_email}. Not legal advice.</p>"
            "</body>"
        )

    @app.get("/api/status")
    def status():
        return eng.status()

    @app.get("/api/languages")
    def languages():
        return languages_payload()

    @app.get("/api/settings")
    def get_settings():
        return {
            "pause_ms": eng.store.get("pause_ms"),
            "pause_min": PAUSE_MIN,
            "pause_max": PAUSE_MAX,
            "target_lang": eng.store.get("target_lang"),
            "partner_lang": eng.store.get("partner_lang"),
            "tts_mode": eng.store.get("tts_mode"),
            "whisper_model": eng.store.get("whisper_model"),
            "whisper_models": list(WHISPER_MODELS),
            "buffer_seconds": s.buffer_seconds,
            "max_segment_s": s.max_segment_s,
        }

    @app.post("/api/settings")
    async def post_settings(request: Request):
        _limit(request)
        data = await request.json()
        kw = {}
        if "pause_ms" in data:
            kw["pause_ms"] = data["pause_ms"]
        if "target_lang" in data:
            kw["target_lang"] = data["target_lang"]
        if "partner_lang" in data:
            kw["partner_lang"] = data["partner_lang"]
        if "tts_mode" in data:
            kw["tts_mode"] = data["tts_mode"]
        if "whisper_model" in data:
            kw["whisper_model"] = data["whisper_model"]
        return eng.store.update(**kw)

    @app.get("/api/ws-token")
    def ws_token():
        uid = eng.store.get("user_id") or "local"
        return {"token": sign(secret, {"u": uid}, "ws", ttl=3600)}

    @app.get("/api/history")
    def history():
        uid = eng.store.get("user_id") or "local"
        return [e.to_dict() for e in eng.history_for(uid).entries()]

    @app.get("/api/history.json")
    def history_json():
        uid = eng.store.get("user_id") or "local"
        return JSONResponse(json.loads(eng.history_for(uid).export_json() or "[]"))

    @app.get("/api/history.txt", response_class=PlainTextResponse)
    def history_txt():
        uid = eng.store.get("user_id") or "local"
        return eng.history_for(uid).export_text()

    @app.post("/api/history/clear")
    def history_clear():
        uid = eng.store.get("user_id") or "local"
        eng.history_for(uid).clear()
        return {"ok": True}

    @app.get("/api/offline/queue")
    def offline_queue():
        return eng.offline.status()

    @app.post("/api/sync")
    def sync():
        return eng.sync()

    @app.post("/api/offline")
    async def force_offline(request: Request):
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        eng.set_force_offline(bool(data.get("value", True)))
        return eng.status()

    # --- Open-core placeholders (future modules) ---
    @app.get("/api/chat")
    def chat_placeholder():
        return JSONResponse(chat_stub(), status_code=501)

    # Link previews + minimal messages (own modules; M4 / voice-note PRs should merge carefully).
    register_unfurl_routes(app, unfurl=unfurl, limiter=unfurl_limiter)
    register_message_routes(
        app, store=messages, unfurl=unfurl, limiter=chat_msg_limiter,
        edit_limiter=chat_edit_limiter,
        hub=app.state.chat_hub,
        translations=app.state.chat_translations,
    )
    register_chat_ws(app)

    @app.get("/api/games")
    def games_placeholder():
        return JSONResponse(games_stub(), status_code=501)

    # --- Commercial boundary: stubs that proxy over HTTP, never import proprietary code ---
    @app.get("/api/commercial/status")
    def commercial_status():
        return eng.commercial.status()

    @app.get("/api/commercial/license")
    def commercial_license():
        uid = eng.store.get("user_id") or "local"
        return eng.commercial.license_status(uid)

    @app.get("/api/commercial/talk-coins")
    def commercial_coins():
        return eng.commercial.talk_coins_balance()

    @app.post("/api/commercial/checkout")
    async def commercial_checkout(request: Request):
        data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        return eng.commercial.checkout(str(data.get("plan", "premium")))

    @app.get("/api/commercial/admin/health")
    def commercial_admin():
        return eng.commercial.admin_health()

    @app.post("/api/commercial/webhook")
    async def commercial_webhook(request: Request):
        raw = await request.body()
        sig = request.headers.get("X-Talking-Signature", "")
        if s.commercial_webhook_secret and not verify_webhook(s.commercial_webhook_secret, raw, sig):
            raise HTTPException(401, "invalid commercial webhook signature")
        if not s.commercial_webhook_secret:
            return {"ok": True, "accepted": False, "reason": "COMMERCIAL_WEBHOOK_SECRET not set"}
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON")
        log.info("commercial webhook event=%s", payload.get("event", "unknown"))
        return {"ok": True, "accepted": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, t: str = ""):
        ip = client_ip(ws)
        if not ws_limiter.allow(ip):
            await ws.close(code=1008, reason="rate limit")
            return
        origin = ws.headers.get("origin")
        host = ws.headers.get("host", "")
        allowed = set(origins) | {f"https://{host}", f"http://{host}"}
        if origin and origin not in allowed:
            await ws.close(code=1008, reason="origin")
            return
        tok = unsign(secret, t, "ws") if t else {"u": eng.store.get("user_id") or "local"}
        if t and not tok:
            await ws.close(code=1008, reason="handshake")
            return
        user_id = (tok or {}).get("u") or "local"
        await ws.accept()
        loop = asyncio.get_running_loop()
        out: asyncio.Queue = asyncio.Queue(maxsize=1000)

        def emit(ev: dict):
            def _put():
                if not out.full():
                    out.put_nowait(ev)
            loop.call_soon_threadsafe(_put)

        sess = eng.new_session(emit, user_id)
        msg_limiter = TokenBucket(60 * 120, burst=400)
        ctl_limiter = TokenBucket(600, burst=60)

        async def sender():
            while True:
                ev = await out.get()
                await ws.send_text(json.dumps(ev, ensure_ascii=False))

        send_task = asyncio.create_task(sender())
        emit(sess.state())
        emit(eng.status())
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
                    kw = {k: data[k] for k in ("target_lang", "partner_lang", "tts_mode", "pause_ms",
                                               "whisper_model", "client_vad") if k in data}
                    emit(sess.configure(**kw))
                elif tp == "start":
                    sess.started = True
                    sess.vad.reset()
                    emit({"type": "started", "session_id": sess.id})
                    emit(eng.status())
                elif tp == "eou":
                    sess.end_of_utterance()
                elif tp == "stop":
                    sess.end_of_utterance()
                    sess.started = False
                    emit({"type": "stopped"})
                elif tp == "swap":
                    emit(sess.configure(target_lang=sess.partner, partner_lang=sess.target))
                elif tp == "sync":
                    loop.run_in_executor(None, eng.sync)
                elif tp == "force_offline":
                    eng.set_force_offline(bool(data.get("value")))
                elif tp == "status":
                    emit(eng.status())
                elif tp == "ping":
                    emit({"type": "pong", "t": data.get("t") if isinstance(data.get("t"), (int, float)) else None})
        except WebSocketDisconnect:
            pass
        finally:
            sess.close()
            send_task.cancel()

    return app
