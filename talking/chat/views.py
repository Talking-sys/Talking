"""Viewer-facing dialog/message shaping for M4 1:1 chat."""
from __future__ import annotations

from typing import Any

from .directory import ChatDirectory
from .models import Dialog, Message
from .store import ChatStore
from .translate_msg import (
    apply_translation,
    public_for_viewer,
    src_lang_for,
    viewer_target_lang,
)
from .translation_cache import MessageTranslationCache


def last_message_public(dialog: Dialog) -> dict[str, Any] | None:
    if not dialog.last_message_id:
        return None
    return {
        "id": dialog.last_message_id,
        "sender_id": dialog.last_sender_id,
        "type": dialog.last_type or "text",
        "text": dialog.last_preview or "",
        "created_at": dialog.updated_at,
    }


def dialog_public(
    store: ChatStore,
    directory: ChatDirectory,
    user_id: str,
    dialog: Dialog,
    *,
    profile=None,
) -> dict[str, Any]:
    profile = profile or directory.get_profile(user_id)
    extra: dict[str, Any] = {
        "unread_count": store.unread_count(user_id, dialog.id) if dialog.kind == "direct" else 0,
        "last_message": last_message_public(dialog),
    }
    prefs = dict((dialog.prefs or {}).get(user_id) or {})
    extra["auto_translate"] = prefs["auto_translate"] if "auto_translate" in prefs else profile.auto_translate
    extra["target_lang"] = prefs.get("target_lang") or profile.native_lang
    if dialog.kind == "direct":
        peer_id = next((p for p in dialog.participants if p != user_id), None)
        extra["peer"] = directory.public_peer(peer_id) if peer_id else None
        extra["peer_id"] = peer_id
        if extra["peer"] and extra["peer"].get("nickname"):
            extra["title"] = extra["peer"]["nickname"]
    return dialog.to_public(viewer_id=user_id, extra=extra)


def viewer_context(
    store: ChatStore,
    directory: ChatDirectory,
    user_id: str,
    dialog_id: str,
    *,
    profile=None,
) -> tuple[bool, str, str | None]:
    """Return (auto_translate, target_lang, peer_id)."""
    profile = profile or directory.get_profile(user_id)
    prefs = store.dialog_pref(user_id, dialog_id)
    auto = prefs["auto_translate"] if "auto_translate" in prefs else profile.auto_translate
    if not store.is_direct(dialog_id):
        auto = False
    tgt = viewer_target_lang(
        profile_lang=profile.native_lang,
        dialog_lang=prefs.get("target_lang"),
    )
    try:
        peer = store.peer_id(user_id, dialog_id)
    except (KeyError, ValueError):
        peer = None
    return bool(auto), tgt, peer


def shape_message(
    msg: Message,
    *,
    viewer_id: str,
    play_url: str | None = None,
    auto: bool = True,
    tgt: str | None = None,
    src: str | None = None,
    translator=None,
    cache: MessageTranslationCache | None = None,
    persist=None,
) -> dict[str, Any]:
    if auto and translator is not None and msg.type == "text" and (msg.text or "").strip():
        apply_translation(
            msg, tgt=tgt or "en", src=src or src_lang_for(msg, None),
            translator=translator, cache=cache, auto=True,
        )
        if persist is not None:
            persist(msg)
    public = public_for_viewer(msg, viewer_id=viewer_id, tgt=tgt, auto=auto)
    if play_url:
        public["play_url"] = play_url
    return public
