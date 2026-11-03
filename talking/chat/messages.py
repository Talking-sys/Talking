"""Minimal text-message model for link previews.

Full chat (unique nicks, WS fan-out, instant translation, auto-delete) is
ROADMAP M4. This store is in-memory, conversation-scoped, and kept in its
own module so a parallel voice-note PR can add `kind="voice_note"` without
rewriting unfurl.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

MAX_TEXT = 500
MAX_PER_CONVERSATION = 200
DEFAULT_CONVERSATION = "local"


@dataclass
class ChatMessage:
    id: str
    conversation_id: str
    sender_id: str
    kind: str
    text: str
    created_at: float
    urls: list[str] = field(default_factory=list)
    preview: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class MessageStore:
    """Process-local ring of messages. M4 should swap in Postgres."""

    def __init__(self, max_per_conversation: int = MAX_PER_CONVERSATION):
        self.max_per_conversation = max_per_conversation
        self._lock = threading.Lock()
        self._by_conv: dict[str, list[ChatMessage]] = {}

    def add(
        self,
        *,
        text: str,
        sender_id: str = "local",
        conversation_id: str = DEFAULT_CONVERSATION,
        kind: str = "text",
        urls: list[str] | None = None,
        preview: dict | None = None,
    ) -> ChatMessage:
        text = (text or "").strip()
        if len(text) > MAX_TEXT:
            raise ValueError("text too long")
        if not text:
            raise ValueError("empty")
        msg = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id or DEFAULT_CONVERSATION,
            sender_id=sender_id or "local",
            kind=kind or "text",
            text=text,
            created_at=time.time(),
            urls=list(urls or []),
            preview=preview,
        )
        with self._lock:
            bucket = self._by_conv.setdefault(msg.conversation_id, [])
            bucket.append(msg)
            overflow = len(bucket) - self.max_per_conversation
            if overflow > 0:
                del bucket[:overflow]
        return msg

    def list(
        self,
        conversation_id: str = DEFAULT_CONVERSATION,
        limit: int = 50,
    ) -> list[ChatMessage]:
        limit = max(1, min(int(limit), self.max_per_conversation))
        with self._lock:
            bucket = list(self._by_conv.get(conversation_id or DEFAULT_CONVERSATION, ()))
        return bucket[-limit:]

    def clear(self, conversation_id: str | None = None) -> None:
        with self._lock:
            if conversation_id is None:
                self._by_conv.clear()
            else:
                self._by_conv.pop(conversation_id, None)
