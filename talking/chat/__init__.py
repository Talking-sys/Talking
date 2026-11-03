"""Chat module (open core). Full chat is ROADMAP M4.

Unified after merging notes (PR #3), link previews (PR #5), and edits (PR #6):

- ``store.ChatStore`` — one file-backed Dialog + Message store
- ``models.Message`` — one model; ``kind`` is canonical, ``type`` is an alias
- ``hub.ChatHub`` + ``/ws/chat`` — one hub (``events.register_chat_ws`` is a no-op)
- ``http.py`` keeps ``GET/POST/PATCH /api/chat/messages`` on the same store
- ``routes.py`` keeps dialog/notes/media paths
- On-demand premium transcription in ``transcribe.py``

Not in this slice: unique nicks, prefix search, groups, block lists, stickers,
polls, 3-minute auto-delete. See ``docs/CHAT_NOTES.md`` and ``docs/CHAT_EDITING.md``.
"""

STAGE = "notes-slice"
STATUS = "partial"
PARTIAL = ["notes", "link_previews", "message_edit", "voice_transcription"]

from .edits import EditError, apply_text_edit, check_can_edit  # noqa: E402,F401
from .gestures import GestureConfig, RecordGestureMachine  # noqa: E402,F401
from .messages import ChatMessage, MessageStore  # noqa: E402,F401


def module_status() -> dict:
    return {
        "ok": True,
        "module": "chat",
        "stage": STAGE,
        "status": STATUS,
        "partial": list(PARTIAL),
        "feature": "notes",
        "message": "Notes slice (voice_note / video_note), link previews, and text edits. "
                   "Full chat is ROADMAP M4.",
    }


def not_implemented() -> dict:
    """Kept so older imports do not break; the notes slice is live."""
    return module_status()
