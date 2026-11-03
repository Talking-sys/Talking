"""Games module (open core). Stage 1 is a placeholder; see docs/ROADMAP.md module M8."""

STAGE = 1
STATUS = "planned"

CATALOG = [
    "chess", "checkers", "giveaway_checkers", "short_backgammon",
    "long_backgammon", "classic_backgammon", "kalah", "ludo", "sic_bo",
]


def not_implemented() -> dict:
    return {"ok": False, "module": "games", "stage": STAGE, "catalog": CATALOG,
            "message": "Games ship in a later module (see docs/ROADMAP.md)."}
