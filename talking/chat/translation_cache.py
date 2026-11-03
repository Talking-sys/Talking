"""Per-message translation cache keyed by message id + target language.

Edits must invalidate any row keyed by the old body so a later translator
cannot keep showing the pre-edit rendering.
"""
from __future__ import annotations

import threading


def _body_key(text: str, src: str, tgt: str) -> str:
    return f"{src}\x1f{tgt}\x1f{text}"


def _msg_key(message_id: str, tgt: str) -> str:
    return f"{message_id}\x1f{tgt}"


class MessageTranslationCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_msg: dict[str, str] = {}
        self._vals: dict[str, str] = {}
        self._by_msg_lang: dict[str, dict] = {}

    def store(self, message_id: str, text: str, src: str, tgt: str, translation: str,
              *, provider: str | None = None, failed: bool = False) -> None:
        k = _body_key(text, src, tgt)
        rec = {
            "text": translation,
            "src": src,
            "tgt": tgt,
            "provider": provider,
            "failed": bool(failed),
        }
        mk = _msg_key(message_id, tgt)
        with self._lock:
            old_rec = self._by_msg_lang.get(mk)
            if old_rec:
                self._vals.pop(_body_key(text, old_rec.get("src") or src, tgt), None)
            old_body = self._by_msg.get(mk)
            if old_body and old_body != k:
                self._vals.pop(old_body, None)
            self._by_msg[mk] = k
            self._vals[k] = translation
            self._by_msg_lang[mk] = rec

    def get(self, text: str, src: str, tgt: str) -> str | None:
        with self._lock:
            return self._vals.get(_body_key(text, src, tgt))

    def get_for_message(self, message_id: str) -> str | None:
        with self._lock:
            for mk, rec in self._by_msg_lang.items():
                if mk.startswith(message_id + "\x1f"):
                    return rec.get("text")
            k = self._by_msg.get(message_id)
            return self._vals.get(k) if k else None

    def get_for(self, message_id: str, tgt: str) -> dict | None:
        with self._lock:
            rec = self._by_msg_lang.get(_msg_key(message_id, tgt))
            return dict(rec) if rec else None

    def invalidate_message(self, message_id: str, old_text: str | None = None) -> None:
        with self._lock:
            drop_lang = [key for key in self._by_msg_lang if key.startswith(message_id + "\x1f")]
            for key in drop_lang:
                rec = self._by_msg_lang.pop(key, None)
                body = self._by_msg.pop(key, None)
                if body:
                    self._vals.pop(body, None)
                if rec:
                    self._vals.pop(_body_key(old_text or "", rec.get("src") or "", rec.get("tgt") or ""), None)
            k = self._by_msg.pop(message_id, None)
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
            self._by_msg_lang.clear()
