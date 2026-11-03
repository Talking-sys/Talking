"""Per-message translation cache.

This branch does not yet attach translations to chat messages (M4 / the
voice-note PR will). Edits must still invalidate any row keyed by the old
body so a later translator cannot keep showing the pre-edit rendering.
"""
from __future__ import annotations

import threading


def _key(text: str, src: str, tgt: str) -> str:
    return f"{src}\x1f{tgt}\x1f{text}"


class MessageTranslationCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_msg: dict[str, str] = {}
        self._vals: dict[str, str] = {}

    def store(self, message_id: str, text: str, src: str, tgt: str, translation: str) -> None:
        k = _key(text, src, tgt)
        keyed = f"{message_id}\x1f{tgt}"
        with self._lock:
            for mid in (message_id, keyed):
                old = self._by_msg.get(mid)
                if old and old != k:
                    # Keep other-language rows for the same message.
                    if mid == message_id:
                        pass
                    else:
                        self._vals.pop(old, None)
                self._by_msg[mid] = k
            self._vals[k] = translation

    def get(self, text: str, src: str, tgt: str) -> str | None:
        with self._lock:
            return self._vals.get(_key(text, src, tgt))

    def get_for_message(self, message_id: str) -> str | None:
        with self._lock:
            k = self._by_msg.get(message_id)
            return self._vals.get(k) if k else None

    def invalidate_message(self, message_id: str, old_text: str | None = None) -> None:
        with self._lock:
            drop_ids = [mid for mid in self._by_msg
                        if mid == message_id or mid.startswith(message_id + "\x1f")]
            for mid in drop_ids:
                k = self._by_msg.pop(mid, None)
                if k:
                    self._vals.pop(k, None)
            if old_text:
                drop = [key for key in self._vals if key.endswith("\x1f" + old_text)]
                for key in drop:
                    self._vals.pop(key, None)
                stale_ids = [mid for mid, key in self._by_msg.items() if key.endswith("\x1f" + old_text)]
                for mid in stale_ids:
                    self._by_msg.pop(mid, None)

    def clear(self) -> None:
        with self._lock:
            self._by_msg.clear()
            self._vals.clear()
