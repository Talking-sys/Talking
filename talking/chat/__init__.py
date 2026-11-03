"""Chat module (open core). Full chat is ROADMAP M4.

This PR adds a minimal in-memory text-message model so link previews have
somewhere to hang, plus Telegram-like text editing. GET /api/chat stays 501.
See chat/messages.py, chat/edits.py, POST/GET/PATCH /api/chat/messages.
A parallel voice-note PR may also touch this package — keep edits additive
(docs/CHAT_EDITING.md).
"""

from .edits import EditError, apply_text_edit, check_can_edit  # noqa: F401
from .messages import ChatMessage, MessageStore  # noqa: F401

STAGE = 1
STATUS = "planned"


def not_implemented() -> dict:
    return {
        "ok": False,
        "module": "chat",
        "stage": STAGE,
        "status": STATUS,
        "partial": ["link_previews", "message_edit"],
        "message": "Full chat ships in a later module (see docs/ROADMAP.md). "
                   "Minimal /api/chat/messages exists for link previews and text edits.",
    }
