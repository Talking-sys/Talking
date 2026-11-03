"""Track authenticated WebSockets so logout / reuse can close them."""
from __future__ import annotations

import logging
from collections import defaultdict

from starlette.websockets import WebSocket

log = logging.getLogger("talking.auth")


class SocketRegistry:
    def __init__(self):
        self._by_user: dict[str, set[WebSocket]] = defaultdict(set)
        self._by_session: dict[str, set[WebSocket]] = defaultdict(set)

    def add(self, ws: WebSocket, user_id: str, session_id: str | None) -> None:
        self._by_user[user_id].add(ws)
        if session_id:
            self._by_session[session_id].add(ws)
        ws.state.auth_user_id = user_id
        ws.state.auth_session_id = session_id

    def discard(self, ws: WebSocket) -> None:
        uid = getattr(ws.state, "auth_user_id", None)
        sid = getattr(ws.state, "auth_session_id", None)
        if uid:
            bucket = self._by_user.get(uid)
            if bucket:
                bucket.discard(ws)
                if not bucket:
                    self._by_user.pop(uid, None)
        if sid:
            bucket = self._by_session.get(sid)
            if bucket:
                bucket.discard(ws)
                if not bucket:
                    self._by_session.pop(sid, None)

    async def close_session(self, session_id: str, *, code: int = 4010, reason: str = "revoked") -> int:
        return await self._close(list(self._by_session.get(session_id) or ()), code, reason)

    async def close_user(self, user_id: str, *, code: int = 4010, reason: str = "revoked") -> int:
        return await self._close(list(self._by_user.get(user_id) or ()), code, reason)

    async def _close(self, sockets: list[WebSocket], code: int, reason: str) -> int:
        n = 0
        for ws in sockets:
            try:
                await ws.close(code=code, reason=reason)
                n += 1
            except Exception:
                log.debug("socket close failed", exc_info=True)
            finally:
                self.discard(ws)
        return n
