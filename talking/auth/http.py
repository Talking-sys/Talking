"""Auth HTTP routes, cookie helpers, and app wiring."""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from talking.db import make_engine, make_session_factory
from talking.security import client_ip, sign

from .apple import build_apple_client, validate_apple_settings
from .crypto import (
    ACCESS_COOKIE,
    ACCESS_COOKIE_DEV,
    ACCESS_TTL_S,
    REFRESH_COOKIE,
    REFRESH_COOKIE_DEV,
    REFRESH_TTL_S,
    load_encryption_key,
    phone_subject,
)
from .deps import AuthMiddleware, read_refresh_cookie, require_user
from .logfilter import install_auth_log_filter
from .phone_util import PhoneError, country_allowed, normalize_phone_channel, parse_e164
from .providers import build_phone_provider, sms_provider_name, twilio_configured
from .rate_limit import AuthRateLimiter
from .service import AuthError, AuthService
from .sockets import SocketRegistry
from .storage import MemoryAuthStorage, PostgresAuthStorage

log = logging.getLogger("talking.auth")


def validate_auth_settings(settings) -> None:
    mode = (settings.auth_mode or "local").strip().lower()
    if mode not in ("local", "accounts"):
        raise RuntimeError("AUTH_MODE must be 'local' or 'accounts'")
    if settings.production and mode == "local":
        raise RuntimeError("AUTH_MODE=local is refused when APP_ENV=production")
    if settings.production and len(settings.secret_key or "") < 32:
        raise RuntimeError("APP_SECRET_KEY required in production (min 32 characters)")
    if settings.production and not (settings.log_salt or "").strip():
        raise RuntimeError("LOG_SALT required in production")
    if settings.production and mode == "accounts" and not (settings.database_url or "").strip():
        raise RuntimeError("DATABASE_URL required when AUTH_MODE=accounts in production")
    if settings.production:
        provider = sms_provider_name(settings) or "console"
        if provider in ("console", ""):
            raise RuntimeError(
                "SMS_PROVIDER=console is refused when APP_ENV=production; "
                "set SMS_PROVIDER=twilio with Twilio Verify credentials"
            )
        if provider not in ("twilio", "twilio_verify"):
            raise RuntimeError(
                f"SMS_PROVIDER={provider} is refused when APP_ENV=production; use twilio"
            )
        if not twilio_configured(settings):
            raise RuntimeError(
                "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_VERIFY_SERVICE_SID "
                "are required when APP_ENV=production"
            )
    validate_apple_settings(settings)


def _https_request(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


def set_session_cookies(response: Response, request: Request, access: str, refresh: str) -> None:
    settings = request.app.state.settings
    secure = settings.production or _https_request(request)
    access_name = ACCESS_COOKIE if secure else ACCESS_COOKIE_DEV
    refresh_name = REFRESH_COOKIE if secure else REFRESH_COOKIE_DEV
    kw = dict(httponly=True, secure=secure, samesite="lax")
    response.set_cookie(access_name, access, max_age=ACCESS_TTL_S, path="/", **kw)
    response.set_cookie(refresh_name, refresh, max_age=REFRESH_TTL_S, path="/api/auth", **kw)
    # Unprefixed aliases in non-production so TestClient / HTTP LAN still send cookies.
    if not settings.production:
        if access_name != ACCESS_COOKIE_DEV:
            response.set_cookie(ACCESS_COOKIE_DEV, access, max_age=ACCESS_TTL_S, path="/", **kw)
        if refresh_name != REFRESH_COOKIE_DEV:
            response.set_cookie(REFRESH_COOKIE_DEV, refresh, max_age=REFRESH_TTL_S, path="/api/auth", **kw)


def clear_session_cookies(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    secure = settings.production or _https_request(request)
    kw = dict(httponly=True, secure=secure, samesite="lax")
    for name, path in (
        (ACCESS_COOKIE, "/"), (ACCESS_COOKIE_DEV, "/"),
        (REFRESH_COOKIE, "/api/auth"), (REFRESH_COOKIE_DEV, "/api/auth"),
    ):
        response.delete_cookie(name, path=path, **kw)


def _err(exc: AuthError) -> HTTPException:
    return HTTPException(exc.status, exc.code)


def _log_salt(settings, secret: bytes) -> bytes:
    raw = (settings.log_salt or "").strip()
    if raw:
        return raw.encode("utf-8") if not isinstance(raw, bytes) else raw
    return secret


def build_auth_stack(settings, secret: bytes):
    validate_auth_settings(settings)
    store: MemoryAuthStorage | PostgresAuthStorage
    engine = None
    if (settings.auth_mode or "local").strip().lower() == "accounts" and settings.database_url:
        engine = make_engine(settings.database_url)
        store = PostgresAuthStorage(make_session_factory(engine))
    else:
        store = MemoryAuthStorage()
    salt = _log_salt(settings, secret)
    enc = load_encryption_key(settings.db_encryption_key, secret)
    allow_debug = (settings.app_env or "").lower() in ("test", "development", "dev", "local")
    svc = AuthService(
        store, secret=secret, log_salt=salt, enc_key=enc,
        phone_provider=build_phone_provider(settings),
        apple=build_apple_client(settings),
        sign_fn=sign, allow_debug_otp=allow_debug,
    )
    limiter = AuthRateLimiter(settings.redis_url)
    sockets = SocketRegistry()
    return store, svc, limiter, sockets, engine


def attach_session_response(request: Request, body: dict, status: int = 200) -> JSONResponse:
    sess = body.pop("session")
    refresh = body.pop("refresh")
    svc: AuthService = request.app.state.auth
    body["csrf_token"] = svc.csrf_token(sess)
    body["user"] = body.get("user")
    resp = JSONResponse(body, status_code=status)
    set_session_cookies(resp, request, svc.access_token(sess), refresh)
    return resp


def register_auth(app: FastAPI) -> None:
    s = app.state.settings
    store, svc, limiter, sockets, engine = build_auth_stack(s, app.state.secret)
    app.state.auth_store = store
    app.state.auth = svc
    app.state.auth_limiter = limiter
    app.state.auth_sockets = sockets
    app.state.auth_engine = engine
    install_auth_log_filter(svc.log_salt)
    app.add_middleware(AuthMiddleware)

    @app.post("/api/auth/recover")
    async def recover(request: Request):
        ip = client_ip(request)
        if not limiter.allow(f"auth:recover:{ip}", 10, 3600):
            raise HTTPException(429, "rate limit")
        data = await _json(request)
        try:
            out = await svc.recover(
                str(data.get("code") or ""), ip=ip, user_agent=request.headers.get("user-agent"),
            )
        except AuthError as e:
            raise _err(e)
        return attach_session_response(request, out)

    @app.post("/api/auth/refresh")
    async def refresh(request: Request):
        raw = read_refresh_cookie(request)
        if not raw:
            raise HTTPException(401, "invalid_refresh")
        try:
            sess, new_raw = await svc.rotate_refresh(raw, ip=client_ip(request))
        except AuthError as e:
            if e.code == "refresh_reuse" and e.user_id:
                await app.state.auth_sockets.close_user(e.user_id)
            raise _err(e)
        user = await svc.store.get_user(sess.user_id)
        body = {"ok": True, "user": svc.public_user(user) if user else None, "session": sess, "refresh": new_raw}
        return attach_session_response(request, body)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        user = require_user(request)
        if user.session is not None:
            await svc.logout(user.session, ip=client_ip(request))
            await app.state.auth_sockets.close_session(user.session_id or "")
        resp = JSONResponse({"ok": True})
        clear_session_cookies(resp, request)
        return resp

    @app.post("/api/auth/logout-all")
    async def logout_all(request: Request):
        user = require_user(request)
        await svc.logout_all(user.id, ip=client_ip(request))
        await app.state.auth_sockets.close_user(user.id)
        resp = JSONResponse({"ok": True})
        clear_session_cookies(resp, request)
        return resp

    @app.get("/api/auth/me")
    async def me(request: Request):
        user = require_user(request)
        if user.auth_mode == "local":
            return {
                "id": user.id, "status": "active", "auth_mode": "local",
                "anonymous": False, "methods": [], "profile_private": True,
            }
        rec = user.user or await svc.store.get_user(user.id)
        methods = await svc.public_methods(user.id)
        csrf = svc.csrf_token(user.session) if user.session is not None else None
        body = svc.public_user(rec, methods=methods, csrf=csrf)
        body["auth_mode"] = "accounts"
        body["anonymous"] = user.is_anonymous
        body["session_id"] = user.session_id
        return body

    @app.get("/api/auth/sessions")
    async def list_sessions(request: Request):
        user = require_user(request)
        if user.auth_mode == "local":
            return {"sessions": []}
        rows = await svc.store.list_sessions(user.id)
        out = []
        for sess in rows:
            item = svc.public_session(sess)
            item["current"] = sess.id == user.session_id
            out.append(item)
        return {"sessions": out}

    @app.delete("/api/auth/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str):
        user = require_user(request)
        try:
            sess = await svc.revoke_session(user.id, session_id)
        except AuthError as e:
            raise _err(e)
        await app.state.auth_sockets.close_session(sess.id)
        return {"ok": True, "id": session_id}

    @app.get("/api/auth/methods")
    async def list_methods(request: Request):
        user = require_user(request)
        if user.auth_mode == "local":
            return {"methods": []}
        return {"methods": await svc.public_methods(user.id)}

    @app.delete("/api/auth/methods/{method_id}")
    async def delete_method(request: Request, method_id: str):
        user = require_user(request)
        try:
            await svc.unlink_method(user.id, method_id)
        except AuthError as e:
            raise _err(e)
        return {"ok": True, "id": method_id}

    @app.post("/api/auth/phone/start")
    async def phone_start(request: Request):
        data = await _json(request)
        try:
            phone = parse_e164(str(data.get("phone") or ""), s.sms_default_region)
        except PhoneError as e:
            raise HTTPException(400, str(e))
        if not country_allowed(phone, s.sms_country_allowlist):
            raise HTTPException(403, "country_not_allowed")
        try:
            channel = normalize_phone_channel(data.get("channel"))
        except PhoneError as e:
            raise HTTPException(400, str(e))
        if s.production and getattr(svc.phone_provider, "name", "") == "console":
            raise HTTPException(503, "sms_unconfigured")
        ip = client_ip(request)
        subj = phone_subject(svc.log_salt, phone)
        if not limiter.allow(f"auth:phone:num:{subj}", 3, 15 * 60):
            raise HTTPException(429, "rate limit")
        if not limiter.allow(f"auth:phone:ip:{ip}", 10, 3600):
            raise HTTPException(429, "rate limit")
        uid = getattr(request.state, "user", None)
        try:
            return await svc.start_phone(
                phone, channel, ip=ip, user_id=(uid.id if uid and uid.authenticated else None),
            )
        except AuthError as e:
            raise _err(e)

    @app.post("/api/auth/phone/verify")
    async def phone_verify(request: Request):
        data = await _json(request)
        try:
            phone = parse_e164(str(data.get("phone") or ""), s.sms_default_region)
        except PhoneError as e:
            raise HTTPException(400, str(e))
        ip = client_ip(request)
        if not limiter.allow(f"auth:phone:verify:{ip}", 20, 3600):
            raise HTTPException(429, "rate limit")
        try:
            out = await svc.verify_phone(
                phone, str(data.get("code") or ""), ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
        except AuthError as e:
            raise _err(e)
        return attach_session_response(request, out)

    @app.post("/api/auth/phone/link")
    async def phone_link(request: Request):
        user = require_user(request)
        if user.auth_mode == "local":
            raise HTTPException(400, "accounts_only")
        data = await _json(request)
        try:
            phone = parse_e164(str(data.get("phone") or ""), s.sms_default_region)
        except PhoneError as e:
            raise HTTPException(400, str(e))
        try:
            return await svc.link_phone(
                user.user, user.session, phone, str(data.get("code") or ""), ip=client_ip(request),
            )
        except AuthError as e:
            raise _err(e)

    @app.get("/api/auth/providers")
    async def auth_providers():
        return {
            "providers": {
                "anonymous": False,
                "phone": True,
                "apple": svc.apple_enabled(),
                "google": False,
                "email": False,
                "passkey": False,
            }
        }

    @app.api_route("/api/auth/apple/start", methods=["GET", "POST"])
    async def apple_start(request: Request):
        if (s.auth_mode or "local").strip().lower() != "accounts":
            raise HTTPException(400, "accounts_only")
        ip = client_ip(request)
        if not limiter.allow(f"auth:apple:ip:{ip}", 10, 3600):
            raise HTTPException(429, "rate limit")
        intent = "signin"
        if request.method == "POST":
            data = await _json(request)
            intent = str(data.get("intent") or request.query_params.get("intent") or "signin")
        else:
            intent = str(request.query_params.get("intent") or "signin")
        uid = None
        if intent == "link":
            if request.method != "POST":
                raise HTTPException(405, "link_requires_post")
            user = require_user(request)
            if user.auth_mode == "local":
                raise HTTPException(400, "accounts_only")
            uid = user.id
        try:
            out = await svc.start_apple(intent=intent, user_id=uid, ip=ip)
        except AuthError as e:
            raise _err(e)
        if _wants_json(request):
            return {"ok": True, "url": out["url"], "intent": out["intent"]}
        return RedirectResponse(out["url"], status_code=302)

    @app.api_route("/api/auth/apple/callback", methods=["GET", "POST"])
    async def apple_callback(request: Request):
        if (s.auth_mode or "local").strip().lower() != "accounts":
            raise HTTPException(400, "accounts_only")
        form = await _apple_callback_payload(request)
        if form.get("error"):
            return _apple_fail(request, AuthError(401, str(form.get("error") or "apple_denied")))
        ip = client_ip(request)
        if not limiter.allow(f"auth:apple:cb:{ip}", 30, 3600):
            raise HTTPException(429, "rate limit")
        user = getattr(request.state, "user", None)
        current = user.user if user and user.authenticated and user.auth_mode == "accounts" else None
        session = user.session if user and user.authenticated else None
        try:
            out = await svc.complete_apple(
                code=str(form.get("code") or ""),
                state=str(form.get("state") or ""),
                user_form=form.get("user"),
                current_user=current,
                session=session,
                ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
        except AuthError as e:
            return _apple_fail(request, e)
        if out.get("link"):
            if _wants_json(request):
                return out
            return RedirectResponse(_apple_return_url(request, "linked"), status_code=303)
        if _wants_json(request):
            return attach_session_response(request, out)
        dest = _apple_return_url(request, "ok")
        resp = RedirectResponse(dest, status_code=303)
        set_session_cookies(resp, request, svc.access_token(out["session"]), out["refresh"])
        return resp

    @app.post("/api/auth/apple/notifications")
    async def apple_notifications(request: Request):
        ip = client_ip(request)
        if not limiter.allow(f"auth:apple:note:{ip}", 60, 3600):
            raise HTTPException(429, "rate limit")
        token = await _apple_notification_jwt(request)
        try:
            out = await svc.handle_apple_notification(token)
        except AuthError as e:
            raise _err(e)
        for uid in out.get("close_users") or []:
            await app.state.auth_sockets.close_user(uid)
        return {"ok": True, "handled": out.get("handled", 0)}


async def _json(request: Request) -> dict:
    if not (request.headers.get("content-type") or "").startswith("application/json"):
        return {}
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "invalid JSON")
    return data


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    return request.query_params.get("format") == "json"


def _apple_return_url(request: Request, status: str, error: str | None = None) -> str:
    origin = (request.app.state.settings.public_origin or "").strip().rstrip("/")
    params = {"auth": status}
    if error:
        params["auth_error"] = error
    qs = urlencode(params)
    if origin:
        return f"{origin}/?{qs}"
    return f"/?{qs}"


def _apple_fail(request: Request, exc: AuthError):
    if _wants_json(request):
        return JSONResponse({"detail": exc.code}, status_code=exc.status)
    return RedirectResponse(_apple_return_url(request, "error", exc.code), status_code=303)


async def _apple_callback_payload(request: Request) -> dict:
    if request.method == "GET":
        return {k: v for k, v in request.query_params.items()}
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        return await _json(request)
    try:
        form = await request.form()
    except Exception:
        return {}
    return {str(k): form.get(k) for k in form.keys()}


async def _apple_notification_jwt(request: Request) -> str:
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        data = await _json(request)
        return str(data.get("payload") or data.get("signedPayload") or "")
    try:
        form = await request.form()
        token = form.get("payload") or form.get("signedPayload")
        if token:
            return str(token)
    except Exception:
        pass
    raw = (await request.body()).decode("utf-8", errors="ignore").strip()
    return raw.strip('"')


