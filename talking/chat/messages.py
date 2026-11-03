"""Minimal text-message model for link previews + Telegram-like edits.

Full chat (unique nicks, WS fan-out, instant translation, auto-delete) is
ROADMAP M4. This store is in-memory, conversation-scoped, and kept in its
own module so a parallel voice-note PR can add `kind="voice_note"` without
rewriting unfurl.

Edit fields (`edited_at`, `edit_count`, private `original_text`/`revisions`)
are the contract PR #3 should copy onto its file-backed ``Message``. Public
``to_dict()`` never includes revision history. See ``docs/CHAT_EDITING.md``.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from .edits import apply_text_edit, check_can_edit, public_edit_fields, validate_edit_text

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
    edited_at: float | None = None
    edit_count: int = 0
    # Server-only: never copied into to_dict().
    original_text: str | None = None
    revisions: list[dict] = field(default_factory=list)
    # Optional translation payload (M4 / PR #3). Cleared on edit.
    translation: str | None = None
    src_lang: str | None = None
    tgt_lang: str | None = None
    translation_provider: str | None = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sender_id": self.sender_id,
            "kind": self.kind,
            "text": self.text,
            "created_at": self.created_at,
            "urls": list(self.urls),
            "preview": self.preview,
            "translation": self.translation,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "translation_provider": self.translation_provider,
        }
        d.update(public_edit_fields(self))
        return d


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
        translation: str | None = None,
        src_lang: str | None = None,
        tgt_lang: str | None = None,
        translation_provider: str | None = None,
        created_at: float | None = None,
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
            created_at=time.time() if created_at is None else float(created_at),
            urls=list(urls or []),
            preview=preview,
            translation=translation,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            translation_provider=translation_provider,
        )
        with self._lock:
            bucket = self._by_conv.setdefault(msg.conversation_id, [])
            bucket.append(msg)
            overflow = len(bucket) - self.max_per_conversation
            if overflow > 0:
                del bucket[:overflow]
        return msg

    def get(
        self,
        message_id: str,
        conversation_id: str | None = None,
    ) -> ChatMessage | None:
        with self._lock:
            if conversation_id:
                bucket = self._by_conv.get(conversation_id, ())
                for m in bucket:
                    if m.id == message_id:
                        return m
                return None
            for bucket in self._by_conv.values():
                for m in bucket:
                    if m.id == message_id:
                        return m
        return None

    def last_own(
        self,
        sender_id: str,
        conversation_id: str = DEFAULT_CONVERSATION,
        *,
        kinds: tuple[str, ...] = ("text",),
    ) -> ChatMessage | None:
        with self._lock:
            bucket = self._by_conv.get(conversation_id or DEFAULT_CONVERSATION, ())
            for m in reversed(bucket):
                if m.sender_id == sender_id and m.kind in kinds:
                    return m
        return None

    def edit_text(
        self,
        message_id: str,
        new_text: str,
        *,
        actor_id: str,
        now: float | None = None,
        window_s: int | float = 0,
        max_text: int = MAX_TEXT,
        conversation_id: str | None = None,
    ) -> tuple[ChatMessage, bool]:
        """Apply a text edit. Returns (msg, changed). Raises EditError."""
        now = time.time() if now is None else float(now)
        cleaned = validate_edit_text(new_text, max_text)
        with self._lock:
            msg = None
            if conversation_id:
                bucket = self._by_conv.get(conversation_id, ())
                for m in bucket:
                    if m.id == message_id:
                        msg = m
                        break
            else:
                for bucket in self._by_conv.values():
                    for m in bucket:
                        if m.id == message_id:
                            msg = m
                            break
                    if msg is not None:
                        break
            check_can_edit(msg, actor_id=actor_id, now=now, window_s=window_s)
            changed = apply_text_edit(msg, cleaned, now=now)
            return msg, changed  # type: ignore[return-value]

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
