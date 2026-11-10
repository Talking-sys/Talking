"""Server-side identity.

Unauthenticated clients are not users. There is no anonymous account.

In ``AUTH_MODE=local`` this process has one operator identity from settings
(``local``). In ``AUTH_MODE=accounts`` identity comes from a signed-in session.
HTTP handlers must use that identity; WebSockets require a signed token
(session cookie or ``/api/ws-token``). Body ``sender_id`` is never identity.
"""
from __future__ import annotations

from fastapi import HTTPException, WebSocket

from .ids import check_id
from .security import unsign

LOCAL_USER_ID = "local"


def server_user_id(app) -> str:
    try:
        uid = app.state.engine.store.get("user_id") or LOCAL_USER_ID
        return check_id(uid)
    except Exception:
        return LOCAL_USER_ID


def request_user_id(request) -> str:
    """Signed-in user, or the local operator. Never invents an anonymous id."""
    user = getattr(getattr(request, "state", None), "user", None)
    if user is not None:
        if not getattr(user, "authenticated", False):
            raise HTTPException(401, "not signed in")
        return user.id
    return server_user_id(request.app)


def reject_sender_spoof(claimed, uid: str) -> None:
    """Body ``sender_id`` is not identity. Mismatch is 403; omitted is fine."""
    if claimed is None:
        return
    text = str(claimed)
    if text and text != uid:
        raise HTTPException(403, "sender_id spoofing")


def origin_allowed(ws: WebSocket, settings) -> bool:
    origins = [
        o.strip().rstrip("/")
        for o in (getattr(settings, "cors_origins", "") or "").split(",")
        if o.strip()
    ]
    pub = (getattr(settings, "public_origin", "") or "").strip().rstrip("/")
    if pub:
        origins.append(pub)
    origin = (ws.headers.get("origin") or "").rstrip("/")
    host = ws.headers.get("host", "")
    allowed = set(origins) | {f"https://{host}", f"http://{host}"}
    # Empty Origin is allowed on WebSockets (non-browser clients). HTTP CSRF
    # rejects empty Origin separately.
    return not origin or origin in allowed


def ws_user_id(app, token: str) -> str | None:
    """User id from a valid HMAC WS token, or None (unauthenticated — not a user)."""
    if not token:
        return None
    tok = unsign(app.state.secret, token, "ws")
    if not tok:
        return None
    raw = str(tok.get("u") or "")
    try:
        uid = check_id(raw) if raw else server_user_id(app)
    except ValueError:
        return None
    if uid != server_user_id(app):
        return None
    return uid


async def handshake_websocket(ws: WebSocket, t: str = "") -> str | None:
    """Origin + token. Returns the user id, or None after closing the socket.

    Never treats a missing token as the local user. When the auth stack is
    mounted, session cookies or session-bound WS tokens are accepted.
    """
    if not origin_allowed(ws, ws.app.state.settings):
        await ws.close(code=1008, reason="origin")
        return None
    ctx = None
    if getattr(ws.app.state, "auth", None) is not None:
        from talking.auth.deps import authenticate_websocket
        ctx = await authenticate_websocket(ws, t)
        uid = ctx.id if ctx is not None and ctx.authenticated else None
    else:
        uid = ws_user_id(ws.app, t)
    if not uid:
        await ws.close(code=1008, reason="handshake")
        return None
    sockets = getattr(ws.app.state, "auth_sockets", None)
    if sockets is not None:
        sockets.add(ws, uid, ctx.session_id if ctx is not None else None)
    return uid
