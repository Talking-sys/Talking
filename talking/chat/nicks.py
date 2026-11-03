"""Unique nicknames (M4): prefix search, 30-day rename, no PII."""
from __future__ import annotations

import re
import unicodedata

NICK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")
NICK_MIN = 3
NICK_MAX = 32
RENAME_SECONDS = 30 * 24 * 3600
SEARCH_LIMIT = 20


class NickError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def fold_nick(raw: str) -> str:
    s = unicodedata.normalize("NFKC", (raw or "").strip())
    return s.casefold()


def validate_nick(raw: str) -> str:
    s = unicodedata.normalize("NFKC", (raw or "").strip())
    if not s or len(s) < NICK_MIN or len(s) > NICK_MAX:
        raise NickError(f"nickname must be {NICK_MIN}–{NICK_MAX} characters")
    if not NICK_RE.match(s):
        raise NickError("nickname must start with a letter and use only letters, digits, underscore")
    return s


def nick_prefix(raw: str) -> str:
    s = unicodedata.normalize("NFKC", (raw or "").strip())
    if not s:
        raise NickError("query required")
    if len(s) > NICK_MAX:
        s = s[:NICK_MAX]
    return s
