"""Extract http(s) URLs from chat message text."""
from __future__ import annotations

import re

# Conservative: require a scheme so we never treat "example.com" as a fetch target.
_URL_RE = re.compile(r'(?i)\bhttps?://[^\s<>\[\]()"\']+')
_TRAIL = ".,;:!?)]}\"'"


def extract_urls(text: str, *, limit: int = 8) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(_TRAIL)
        if u.count("(") < u.count(")"):
            u = u.rstrip(")")
        if len(u) < 10 or u in seen:
            continue
        seen.add(u)
        found.append(u)
        if len(found) >= limit:
            break
    return found
