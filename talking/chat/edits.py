"""Telegram-like message edit policy.

Used by the in-memory preview store (this PR) and designed so PR #3's
file-backed ``Message`` (which uses ``type`` instead of ``kind``) can call
the same helpers without a rewrite. See ``docs/CHAT_EDITING.md``.
"""
from __future__ import annotations

from typing import Any

# Text bodies. Captions (voice/video notes, later photos) are listed but
# not enabled until a caption field is actually collected in the UI.
EDITABLE_KINDS = frozenset({"text"})
CAPTION_KINDS = frozenset({
    "voice_note", "video_note", "photo", "video", "document", "animation",
})
DEFAULT_EDIT_WINDOW_S = 48 * 3600  # Telegram ~48 h from original send.
MAX_REVISIONS = 50


class EditError(Exception):
    status = 400
    code = "edit_error"

    def __init__(self, message: str = "cannot edit"):
        super().__init__(message)
        self.message = message


class MessageNotFound(EditError):
    status = 404
    code = "not_found"

    def __init__(self, message: str = "message not found"):
        super().__init__(message)


class NotAuthor(EditError):
    status = 403
    code = "not_author"

    def __init__(self, message: str = "not the author"):
        super().__init__(message)


class EditWindowExpired(EditError):
    status = 409
    code = "window_expired"

    def __init__(self, message: str = "edit window expired"):
        super().__init__(message)


class UneditableKind(EditError):
    status = 422
    code = "uneditable_kind"

    def __init__(self, message: str = "kind is not editable"):
        super().__init__(message)


class InvalidText(EditError):
    status = 422
    code = "invalid_text"

    def __init__(self, message: str = "invalid text"):
        super().__init__(message)


def message_kind(msg: Any) -> str:
    """PR #5 uses ``kind``; PR #3 uses ``type``. Accept either."""
    return getattr(msg, "kind", None) or getattr(msg, "type", None) or "text"


def window_expired(created_at: float, now: float, window_s: int | float) -> bool:
    """``window_s <= 0`` means unlimited (tests / operator override)."""
    if window_s is None or float(window_s) <= 0:
        return False
    return (now - created_at) > float(window_s)


def validate_edit_text(text: Any, max_len: int) -> str:
    if text is None:
        raise InvalidText("empty")
    if not isinstance(text, str):
        raise InvalidText("text required")
    if len(text) > max_len:
        raise InvalidText("text too long")
    stripped = text.strip()
    if not stripped:
        raise InvalidText("empty")
    return stripped


def check_can_edit(
    msg: Any,
    *,
    actor_id: str,
    now: float,
    window_s: int | float,
    editable_kinds: frozenset[str] | None = None,
) -> None:
    if msg is None:
        raise MessageNotFound("message not found")
    if (actor_id or "") != (getattr(msg, "sender_id", None) or ""):
        raise NotAuthor("not the author")
    kinds = editable_kinds if editable_kinds is not None else EDITABLE_KINDS
    if message_kind(msg) not in kinds:
        raise UneditableKind("kind is not editable")
    if window_expired(float(getattr(msg, "created_at", 0) or 0), now, window_s):
        raise EditWindowExpired("edit window expired")


def clear_translation_fields(msg: Any) -> None:
    """Drop rendered translation so a later cache cannot serve stale text."""
    for name in ("translation", "src_lang", "tgt_lang", "translation_provider"):
        if hasattr(msg, name):
            setattr(msg, name, None)


def apply_text_edit(msg: Any, new_text: str, *, now: float) -> bool:
    """Mutate ``msg`` in place. Returns False if the text is unchanged (no-op).

    Stores the previous body on ``original_text`` / ``revisions`` for the
    server. Callers must never put those fields on the public wire payload.
    """
    current = getattr(msg, "text", "") or ""
    if new_text == current:
        return False
    if getattr(msg, "original_text", None) is None:
        msg.original_text = current
    revisions = list(getattr(msg, "revisions", None) or [])
    revisions.append({"text": current, "at": getattr(msg, "edited_at", None) or getattr(msg, "created_at", now)})
    if len(revisions) > MAX_REVISIONS:
        revisions = revisions[-MAX_REVISIONS:]
    msg.revisions = revisions
    msg.text = new_text
    msg.edited_at = now
    msg.edit_count = int(getattr(msg, "edit_count", 0) or 0) + 1
    clear_translation_fields(msg)
    return True


def first_url_changed(old_urls: list[str] | None, new_urls: list[str] | None) -> bool:
    old = (old_urls or [None])[:1]
    new = (new_urls or [None])[:1]
    return (old[0] if old else None) != (new[0] if new else None)


def public_edit_fields(msg: Any) -> dict:
    """Fields safe to show anyone. Never includes original_text / revisions."""
    edited_at = getattr(msg, "edited_at", None)
    return {
        "edited_at": edited_at,
        "edit_count": int(getattr(msg, "edit_count", 0) or 0),
        "edited": edited_at is not None,
    }
