"""Chat module (open core). Full chat is ROADMAP M4.

This PR adds only a minimal in-memory text-message model so link previews
have somewhere to hang. GET /api/chat stays 501. See chat/messages.py and
POST/GET /api/chat/messages. A parallel voice-note PR may also touch this
package — keep edits additive.
"""

from .messages import ChatMessage, MessageStore  # noqa: F401

STAGE = 1
STATUS = "planned"


def not_implemented() -> dict:
    return {
        "ok": False,
        "module": "chat",
        "stage": STAGE,
        "status": STATUS,
        "partial": ["link_previews"],
        "message": "Full chat ships in a later module (see docs/ROADMAP.md). "
                   "Minimal /api/chat/messages exists for link previews.",
    }
