"""Persistent UI/session settings (pause, languages, model)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .config import DEFAULT_WHISPER_MODEL, WHISPER_MODELS

PAUSE_MIN, PAUSE_MAX = 300, 3000

DEFAULTS = {
    "pause_ms": 800,
    "target_lang": "en",
    "partner_lang": "ru",
    "tts_mode": "browser",
    "whisper_model": DEFAULT_WHISPER_MODEL,
    "recent_targets": ["en", "ru", "es", "de"],
    "user_id": None,
}


def clamp_pause(v) -> int:
    try:
        v = int(round(float(v) / 100.0) * 100)
    except (TypeError, ValueError):
        return DEFAULTS["pause_ms"]
    return max(PAUSE_MIN, min(PAUSE_MAX, v))


def clamp_model(v) -> str:
    if isinstance(v, str) and v in WHISPER_MODELS:
        return v
    return DEFAULT_WHISPER_MODEL


class SettingsStore:
    def __init__(self, path: str | Path, overrides: dict | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass
        for k, v in (overrides or {}).items():
            if v is not None:
                self.data[k] = v
        env_uid = os.environ.get("USER_ID")
        if env_uid:
            self.data["user_id"] = env_uid
        if not self.data.get("user_id"):
            # Single-user local mode. Opaque UUIDs are minted in AUTH_MODE=accounts.
            self.data["user_id"] = "local"
        self.data["pause_ms"] = clamp_pause(self.data.get("pause_ms"))
        self.data["whisper_model"] = clamp_model(self.data.get("whisper_model"))
        self.save()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, **kw) -> dict:
        with self._lock:
            if "pause_ms" in kw and kw["pause_ms"] is not None:
                kw["pause_ms"] = clamp_pause(kw["pause_ms"])
            if "whisper_model" in kw and kw["whisper_model"] is not None:
                kw["whisper_model"] = clamp_model(kw["whisper_model"])
            for k, v in kw.items():
                if v is not None and k in DEFAULTS:
                    self.data[k] = v
            if kw.get("target_lang"):
                rec = [kw["target_lang"]] + [x for x in self.data.get("recent_targets", []) if x != kw["target_lang"]]
                self.data["recent_targets"] = rec[:6]
            self.save()
            return dict(self.data)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
