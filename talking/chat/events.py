"""In-process fan-out for chat events (``message_edited``).

PR #3 already ships ``talking/chat/hub.py`` + ``/ws/chat``. This module is
intentionally the same shape: ``subscribe`` / ``unsubscribe`` / ``publish``.
On merge, keep a single hub on ``app.state.chat_hub`` (prefer PR #3's file)
and skip registering ``/ws/chat`` a second time — ``register_chat_ws`` is
idempotent.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from talking.security import unsign

log = logging.getLogger(__name__)


class ChatHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, key: str, queue: asyncio.Queue) -> None:
        self._subs[key].add(queue)

    def unsubscribe(self, key: str, queue: asyncio.Queue) -> None:
        bucket = self._subs.get(key)
        if not bucket:
            return
        bucket.discard(queue)
        if not bucket:
            self._subs.pop(key, None)

    def publish(self, key: str, event: dict) -> None:
        for q in list(self._subs.get(key) or ()):
            try:
                if not q.full():
                    q.put_nowait(event)
            except Exception:
                log.debug("chat hub drop", exc_info=True)

    def publish_edit(self, msg_public: dict) -> None:
        event = {"type": "message_edited", "message": msg_public}
        keys = {
            "*",
            str(msg_public.get("sender_id") or ""),
            str(msg_public.get("conversation_id") or msg_public.get("dialog_id") or ""),
        }
        for key in keys:
            if key:
                self.publish(key, event)


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
    """``/ws/chat`` — skipped if a notes/M4 PR already registered it."""
    if _ws_chat_registered(app):
        return
    hub = ensure_hub(app)
    secret = app.state.secret

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, t: str = ""):
        s = app.state.settings
        origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
        origin = ws.headers.get("origin")
        host = ws.headers.get("host", "")
        allowed = set(origins) | {f"https://{host}", f"http://{host}"}
        if origin and origin not in allowed:
            await ws.close(code=1008, reason="origin")
            return
        uid = "local"
        try:
            uid = app.state.engine.store.get("user_id") or "local"
        except Exception:
            uid = "local"
        tok = unsign(secret, t, "ws") if t else {"u": uid}
        if t and not tok:
            await ws.close(code=1008, reason="handshake")
            return
        uid = (tok or {}).get("u") or uid or "local"
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        hub.subscribe(uid, q)
        hub.subscribe("*", q)

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
            hub.unsubscribe(uid, q)
            hub.unsubscribe("*", q)
            send_task.cancel()
