"""Apply instant chat translation for a viewer (cache per message + target lang)."""
from __future__ import annotations

import logging
from typing import Any

from talking.commands import parse_command
from talking.languages import WHISPER_LANGUAGES

from .models import Message
from .translation_cache import MessageTranslationCache

log = logging.getLogger(__name__)


def viewer_target_lang(
    *,
    profile_lang: str | None,
    dialog_lang: str | None,
    fallback: str = "en",
) -> str:
    for cand in (dialog_lang, profile_lang, fallback):
        if cand and cand in WHISPER_LANGUAGES:
            return cand
    return "en"


def src_lang_for(msg: Message, sender_native: str | None) -> str:
    for cand in (msg.src_lang, sender_native, "en"):
        if cand and cand in WHISPER_LANGUAGES:
            return cand
    return "en"


def skip_translation(text: str) -> bool:
    """Owner spec: command filter — do not send translator-commands through MT."""
    return parse_command(text) is not None


def cached_row(msg: Message, tgt: str, cache: MessageTranslationCache | None) -> dict[str, Any] | None:
    if cache is not None:
        rec = cache.get_for(msg.id, tgt)
        if rec:
            return rec
    stored = (msg.translations or {}).get(tgt)
    if isinstance(stored, dict) and stored.get("text") is not None:
        return stored
    if cache is not None and msg.text:
        body = cache.get(msg.text, msg.src_lang or "", tgt)
        if body is not None:
            return {"text": body, "tgt": tgt, "failed": False}
    return None


def apply_translation(
    msg: Message,
    *,
    tgt: str,
    src: str,
    translator,
    cache: MessageTranslationCache | None = None,
    auto: bool = True,
) -> Message:
    """Fill viewer's translation on the message. Always attempts when auto=True.

    On API failure the original is kept with ``translation_failed``.
    Same-language pairs still go through the translator (owner spec).
    """
    if not auto or msg.type != "text" or not (msg.text or "").strip():
        return msg
    if skip_translation(msg.text):
        msg.extra = dict(msg.extra or {})
        msg.extra["command"] = True
        return msg
    rec = cached_row(msg, tgt, cache)
    if rec is None:
        try:
            tr = translator.translate(msg.text, src, tgt, always=True)
            rec = {
                "text": tr.text,
                "src": src,
                "tgt": tgt,
                "provider": getattr(tr, "provider", None),
                "failed": False,
            }
        except Exception as e:  # noqa: BLE001
            log.info("chat translate failed: %s", type(e).__name__)
            rec = {
                "text": msg.text,
                "src": src,
                "tgt": tgt,
                "provider": None,
                "failed": True,
                "reason": str(e)[:200],
            }
        bucket = dict(msg.translations or {})
        bucket[tgt] = rec
        msg.translations = bucket
        if cache is not None:
            cache.store(
                msg.id, msg.text, src, tgt, rec["text"],
                provider=rec.get("provider"), failed=bool(rec.get("failed")),
            )
    msg.src_lang = rec.get("src") or src
    msg.tgt_lang = tgt
    msg.translation = rec.get("text")
    msg.translation_provider = rec.get("provider")
    if rec.get("failed"):
        msg.extra = dict(msg.extra or {})
        msg.extra["translation_failed"] = True
    return msg


def public_for_viewer(msg: Message, *, viewer_id: str, tgt: str | None, auto: bool) -> dict[str, Any]:
    d = msg.to_public(viewer_id=viewer_id)
    if not str(msg.dialog_id).startswith("dm"):
        return d
    failed = bool((msg.extra or {}).get("translation_failed"))
    command = bool((msg.extra or {}).get("command"))
    mine = viewer_id == msg.sender_id
    # Author always sees their original; the peer sees the translation.
    effective_auto = auto and not mine
    translation = d.get("translation")
    original = d.get("text") or ""
    show_original = bool(
        effective_auto and not command and translation and original and translation != original
    )
    d["original"] = original if show_original or (failed and effective_auto) else None
    d["show_original"] = show_original or (failed and effective_auto)
    d["translation_failed"] = failed and effective_auto
    d["command"] = command
    if tgt:
        d["tgt_lang"] = tgt
    if not effective_auto or command:
        d["translation"] = None
        d["show_original"] = False
        d["original"] = None
        d["translation_failed"] = False
    return d
