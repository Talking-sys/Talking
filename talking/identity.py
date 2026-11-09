"""Server-side identity for the single-user open core.

Unauthenticated clients are not users. Cookie sessions belong to M10 (PR #9).
Until then this process has one operator identity from settings (``local``).
HTTP handlers must use that identity; WebSockets require a signed token.
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
    return server_user_id(request.app)


def reject_sender_spoof(claimed, uid: str) -> None:
    """Body ``sender_id`` is not identity. Mismatch is 403; omitted is fine."""
    if claimed is None:
        return
    text = str(claimed)
    if text and text != uid:
        raise HTTPException(403, "sender_id spoofing")


def origin_allowed(ws: WebSocket, settings) -> bool:
    origins = [o.strip() for o in (getattr(settings, "cors_origins", "") or "").split(",") if o.strip()]
    origin = ws.headers.get("origin")
    host = ws.headers.get("host", "")
    allowed = set(origins) | {f"https://{host}", f"http://{host}"}
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
    """Origin + token. Returns the user id, or None after closing the socket."""
    if not origin_allowed(ws, ws.app.state.settings):
        await ws.close(code=1008, reason="origin")
        return None
    uid = ws_user_id(ws.app, t)
    if uid is None:
        await ws.close(code=1008, reason="handshake")
        return None
    return uid
