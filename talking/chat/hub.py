"""In-process fan-out for /ws/chat. M4 can swap in Redis pubsub."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

log = logging.getLogger(__name__)


class ChatHub:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        self._subs[user_id].add(queue)

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        bucket = self._subs.get(user_id)
        if not bucket:
            return
        bucket.discard(queue)
        if not bucket:
            self._subs.pop(user_id, None)

    def publish(self, user_id: str, event: dict) -> None:
        for q in list(self._subs.get(user_id) or ()):
            try:
                if not q.full():
                    q.put_nowait(event)
            except Exception:
                log.debug("chat hub drop", exc_info=True)
