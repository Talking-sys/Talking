"""Chat module (open core). Stage 1 is a placeholder; see docs/ROADMAP.md module M4."""

STAGE = 1
STATUS = "planned"


def not_implemented() -> dict:
    return {"ok": False, "module": "chat", "stage": STAGE,
            "message": "Chat ships in a later module (see docs/ROADMAP.md)."}
