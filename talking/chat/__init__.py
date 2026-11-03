"""Minimal GPL chat plumbing for Telegram-style notes (кружочки).

This is a thin slice so ROADMAP M4 (full chat) can extend it:

- ``store.ChatStore`` — file-backed Dialog + Message (swap for Postgres later)
- ``media.MediaLibrary`` — magic-byte sniff, UUID names, per-user quota
- ``media.DownloadTokens`` — expiring play tokens + one-time downloads
- ``gestures.RecordGestureMachine`` — hold-to-record / slide-to-cancel / lock
- HTTP + ``/ws/chat`` in ``routes.py``

Not in this slice: unique nicks, prefix search, groups, block lists, stickers,
polls, 3-minute auto-delete. See ``docs/CHAT_NOTES.md``.
"""

STAGE = "notes-slice"
STATUS = "partial"

from .gestures import GestureConfig, RecordGestureMachine  # noqa: E402


def module_status() -> dict:
    return {
        "ok": True,
        "module": "chat",
        "stage": STAGE,
        "status": STATUS,
        "message": "Notes slice (voice_note / video_note). Full chat is ROADMAP M4.",
    }


def not_implemented() -> dict:
    """Kept so older imports do not break; the notes slice is live."""
    return module_status()
