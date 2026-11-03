"""Safe identifiers and paths (no traversal, no secrets in logs)."""
from __future__ import annotations

import os
import re
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def secure_dir(p: str | Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except PermissionError:
        pass
    return p


def check_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValueError(f"invalid {what}")
    return value


def safe_join(root: str | Path, *parts: str) -> Path:
    root = Path(root).resolve()
    p = root.joinpath(*parts).resolve()
    if not p.is_relative_to(root):
        raise ValueError("path escapes root")
    return p
