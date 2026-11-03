"""current_user resolution, CSRF, CORS origin checks, WebSocket handshake."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from talking.ids import check_id
from talking.security import client_ip, unsign

from .capabilities import identities_are_anonymous
from .crypto import ACCESS_COOKIE, ACCESS_COOKIE_DEV, CSRF_HEADER, REFRESH_COOKIE, REFRESH_COOKIE_DEV

log = logging.getLogger("talking.auth")

CSRF_EXEMPT_PREFIXES = (
    "/api/sdk/",
    "/api/commercial/webhook",
    "/healthz",
    "/readyz",
    "/static",
)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class UserContext:
    id: str
    authenticated: bool
    auth_mode: str
    status: str = "active"
    session_id: str | None = None
    session: object | None = None
    user: object | None = None
    is_anonymous: bool = True

    @property
    def local(self) -> bool:
        return self.auth_mode == "local"


def local_user_id(app) -> str:
    try:
        uid = app.state.engine.store.get("user_id") or "local"
        return check_id(uid)
    except Exception:
        return "local"


def is_accounts(settings) -> bool:
    return (getattr(settings, "auth_mode", "local") or "local").strip().lower() == "accounts"


def origin_allowlist(settings, request: Request | WebSocket | None = None) -> set[str]:
    origins = [o.strip().rstrip("/") for o in (settings.cors_origins or "").split(",") if o.strip()]
    pub = (getattr(settings, "public_origin", "") or "").strip().rstrip("/")
    if pub:
        origins.append(pub)
    if request is not None:
        host = request.headers.get("host", "")
        if host:
            origins.append(f"https://{host}")
            origins.append(f"http://{host}")
    return {o for o in origins if o}


def origin_ok(request: Request | WebSocket, settings) -> bool:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin:
        return True
    return origin in origin_allowlist(settings, request)


def csrf_site_ok(request: Request) -> bool:
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if not site:
        return True
    return site in ("same-origin", "same-site", "none")


def read_access_cookie(request: Request | WebSocket) -> str:
    cookies = request.cookies
    return cookies.get(ACCESS_COOKIE) or cookies.get(ACCESS_COOKIE_DEV) or ""


def read_refresh_cookie(request: Request) -> str:
    cookies = request.cookies
    return cookies.get(REFRESH_COOKIE) or cookies.get(REFRESH_COOKIE_DEV) or ""


async def resolve_user(request: Request) -> UserContext:
    app = request.app
    settings = app.state.settings
    mode = (settings.auth_mode or "local").strip().lower()
    if mode != "accounts":
        return UserContext(
            id=local_user_id(app), authenticated=True, auth_mode="local", is_anonymous=True,
        )
    svc = app.state.auth
    raw = read_access_cookie(request)
    payload = unsign(app.state.secret, raw, "access") if raw else None
    loaded = await svc.load_access(payload) if payload else None
    if loaded is None:
        return UserContext(id="", authenticated=False, auth_mode="accounts")
    user, sess = loaded
    methods = await svc.store.list_identities(user.id)
    anon = identities_are_anonymous(methods)
    return UserContext(
        id=user.id, authenticated=True, auth_mode="accounts", status=user.status,
        session_id=sess.id, session=sess, user=user, is_anonymous=anon,
    )


def require_user(request: Request) -> UserContext:
    user = getattr(request.state, "user", None)
    if user is None or not user.authenticated:
        raise HTTPException(401, "not signed in")
    return user


async def authenticate_websocket(ws: WebSocket, t: str = "") -> UserContext | None:
    """Return a user context, or None if the socket should be closed (caller closes)."""
    app = ws.app
    settings = app.state.settings
    origins = origin_allowlist(settings, ws)
    origin = (ws.headers.get("origin") or "").rstrip("/")
    if origin and origin not in origins:
        return None
    mode = (settings.auth_mode or "local").strip().lower()
    secret = app.state.secret

    raw_cookie = read_access_cookie(ws)
    if raw_cookie:
        payload = unsign(secret, raw_cookie, "access")
        if payload:
            loaded = await app.state.auth.load_access(payload)
            if loaded is not None:
                user, sess = loaded
                methods = await app.state.auth.store.list_identities(user.id)
                return UserContext(
                    id=user.id, authenticated=True, auth_mode=mode, session_id=sess.id,
                    session=sess, user=user, is_anonymous=identities_are_anonymous(methods),
                )
            return None

    if t:
        tok = unsign(secret, t, "ws")
        if not tok:
            return None
        uid = str(tok.get("u") or "")
        sid = str(tok.get("sid") or "")
        if mode == "accounts":
            if not sid:
                return None
            loaded = await app.state.auth.load_access({"sid": sid, "v": tok.get("v"), "u": uid})
            if loaded is None:
                return None
            user, sess = loaded
            methods = await app.state.auth.store.list_identities(user.id)
            return UserContext(
                id=user.id, authenticated=True, auth_mode="accounts", session_id=sess.id,
                session=sess, user=user, is_anonymous=identities_are_anonymous(methods),
            )
        try:
            uid = check_id(uid) if uid else local_user_id(app)
        except ValueError:
            uid = local_user_id(app)
        return UserContext(id=uid, authenticated=True, auth_mode="local")

    if mode == "accounts":
        return None
    return UserContext(id=local_user_id(app), authenticated=True, auth_mode="local")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        request.state.user = await resolve_user(request)
        if is_accounts(settings) and request.method not in SAFE_METHODS:
            path = request.url.path
            if not any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
                denied = _csrf_reject(request, settings)
                if denied is not None:
                    return denied
        return await call_next(request)


def _csrf_reject(request: Request, settings) -> Response | None:
    if not origin_ok(request, settings):
        return JSONResponse({"detail": "csrf origin"}, status_code=403)
    if not csrf_site_ok(request):
        return JSONResponse({"detail": "csrf site"}, status_code=403)
    user: UserContext = request.state.user
    # Cookie-backed mutating calls need the CSRF header. Anonymous start/verify
    # still get Origin + Sec-Fetch-Site (login CSRF).
    if user.authenticated and user.session_id:
        header = request.headers.get(CSRF_HEADER) or request.headers.get("x-csrf-token") or ""
        svc = request.app.state.auth
        expect = svc.csrf_token(user.session)
        given = unsign(request.app.state.secret, header, "csrf") if header else None
        if not given or given.get("sid") != user.session_id:
            # also accept the signed token compared directly
            if not header or header != expect:
                return JSONResponse({"detail": "csrf token"}, status_code=403)
    return None
