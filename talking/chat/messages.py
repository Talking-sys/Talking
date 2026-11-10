"""Text-message API over the unified chat store.

``MessageStore`` is the adapter used by ``GET/POST/PATCH /api/chat/messages``.
In production it writes to the file-backed ``ChatStore`` (same JSONL as notes).
Tests may construct an in-memory store without a ``ChatStore``.

``ChatMessage`` is an alias of ``models.Message`` (``kind`` canonical, ``type``
alias). Public ``to_dict()`` never includes revision history.
"""
from __future__ import annotations

import threading
import time
import uuid

from .edits import apply_text_edit, check_can_edit, message_kind, validate_edit_text
from .models import Message

MAX_TEXT = 500
MAX_PER_CONVERSATION = 200
DEFAULT_CONVERSATION = "local"

ChatMessage = Message


class MessageStore:
    """Conversation-scoped messages. Backed by ChatStore when attached to the app."""

    def __init__(
        self,
        max_per_conversation: int = MAX_PER_CONVERSATION,
        chat_store=None,
        user_id: str = "local",
    ):
        self.max_per_conversation = max_per_conversation
        self._chat = chat_store
        self._user_id = user_id or "local"
        self._lock = threading.RLock()
        self._by_conv: dict[str, list[Message]] = {}

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
        user_id: str | None = None,
    ) -> Message:
        text = (text or "").strip()
        if len(text) > MAX_TEXT:
            raise ValueError("text too long")
        if not text:
            raise ValueError("empty")
        conv = conversation_id or DEFAULT_CONVERSATION
        owner = user_id or self._user_id
        msg = Message(
            id=str(uuid.uuid4()),
            dialog_id=conv,
            sender_id=sender_id or "local",
            type=kind or "text",
            created_at=time.time() if created_at is None else float(created_at),
            text=text,
            urls=list(urls or []),
            preview=preview,
            translation=translation,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            translation_provider=translation_provider,
        )
        if self._chat is not None:
            self._chat.ensure_default_dialog(owner)
            try:
                self._chat.get_dialog(owner, msg.dialog_id)
            except KeyError:
                if getattr(self._chat, "is_direct", lambda _d: False)(msg.dialog_id):
                    raise KeyError("dialog not found")
                if msg.dialog_id == DEFAULT_CONVERSATION:
                    self._chat.ensure_default_dialog(owner)
                else:
                    created = self._chat.create_dialog(owner, msg.dialog_id)
                    # Keep the caller-supplied conversation id on the message;
                    # create_dialog mints a new id, so fall back to in-memory
                    # only when the dialog id is not the default.
                    if created.id != msg.dialog_id:
                        with self._lock:
                            bucket = self._by_conv.setdefault(msg.dialog_id, [])
                            bucket.append(msg)
                            overflow = len(bucket) - self.max_per_conversation
                            if overflow > 0:
                                del bucket[:overflow]
                        return msg
            self._chat.add_message(owner, msg)
            return msg
        with self._lock:
            bucket = self._by_conv.setdefault(msg.dialog_id, [])
            bucket.append(msg)
            overflow = len(bucket) - self.max_per_conversation
            if overflow > 0:
                del bucket[:overflow]
        return msg

    def get(
        self,
        message_id: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> Message | None:
        owner = user_id or self._user_id
        if self._chat is not None:
            if conversation_id:
                try:
                    return self._chat.get_message(owner, conversation_id, message_id)
                except (KeyError, ValueError):
                    return None
            found = self._chat.find_message(owner, message_id)
            if found is not None:
                return found
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
    ) -> Message | None:
        conv = conversation_id or DEFAULT_CONVERSATION
        if self._chat is not None:
            try:
                bucket = self._chat.list_messages(self._user_id, conv, limit=0)
            except (KeyError, ValueError):
                bucket = []
            for m in reversed(bucket):
                if m.sender_id == sender_id and message_kind(m) in kinds:
                    return m
        with self._lock:
            bucket = self._by_conv.get(conv, ())
            for m in reversed(bucket):
                if m.sender_id == sender_id and message_kind(m) in kinds:
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
        user_id: str | None = None,
    ) -> tuple[Message, bool]:
        """Apply a text edit. Returns (msg, changed). Raises EditError."""
        now = time.time() if now is None else float(now)
        cleaned = validate_edit_text(new_text, max_text)
        owner = user_id or self._user_id
        with self._lock:
            msg = self.get(message_id, conversation_id, user_id=owner)
            check_can_edit(msg, actor_id=actor_id, now=now, window_s=window_s)
            changed = apply_text_edit(msg, cleaned, now=now)
            if changed and self._chat is not None and msg is not None:
                self._chat.update_message(owner, msg)
            return msg, changed  # type: ignore[return-value]

    def list(
        self,
        conversation_id: str = DEFAULT_CONVERSATION,
        limit: int = 50,
        user_id: str | None = None,
    ) -> list[Message]:
        limit = max(1, min(int(limit), self.max_per_conversation))
        conv = conversation_id or DEFAULT_CONVERSATION
        owner = user_id or self._user_id
        if self._chat is not None:
            try:
                self._chat.ensure_default_dialog(owner)
                rows = self._chat.list_messages(owner, conv, limit=limit)
            except (KeyError, ValueError):
                rows = []
            extra = self._by_conv.get(conv, [])
            if extra:
                rows = rows + extra
                rows = rows[-limit:]
            return rows
        with self._lock:
            bucket = list(self._by_conv.get(conv, ()))
        return bucket[-limit:]

    def clear(self, conversation_id: str | None = None) -> None:
        with self._lock:
            if conversation_id is None:
                self._by_conv.clear()
            else:
                self._by_conv.pop(conversation_id, None)
