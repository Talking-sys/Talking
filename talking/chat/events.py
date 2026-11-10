"""Chat hub helpers. Prefer ``hub.ChatHub``; this module re-exports it.

``register_chat_ws`` is idempotent so notes ``register_chat`` can own ``/ws/chat``.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from talking.identity import handshake_websocket

from .hub import ChatHub

log = logging.getLogger(__name__)

__all__ = ["ChatHub", "ensure_hub", "register_chat_ws"]


def ensure_hub(app: FastAPI) -> ChatHub:
    hub = getattr(app.state, "chat_hub", None)
    if hub is None:
        hub = ChatHub()
        app.state.chat_hub = hub
    return hub


def _ws_chat_registered(app: FastAPI) -> bool:
    for route in app.routes:
        if getattr(route, "path", None) == "/ws/chat":
            return True
    return False


def register_chat_ws(app: FastAPI) -> None:
    """``/ws/chat`` — skipped if notes/M4 already registered it."""
    if _ws_chat_registered(app):
        return
    hub = ensure_hub(app)

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, t: str = ""):
        uid = await handshake_websocket(ws, t)
        if uid is None:
            return
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        hub.subscribe(uid, q)

        async def sender():
            while True:
                ev = await q.get()
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
            sockets = getattr(app.state, "auth_sockets", None)
            if sockets is not None:
                sockets.discard(ws)
            hub.unsubscribe(uid, q)
            send_task.cancel()
