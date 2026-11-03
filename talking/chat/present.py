"""Viewer-facing chat payloads, group translation, and member-only fan-out."""
from __future__ import annotations

import logging
from typing import Any

from talking.commands import parse_command
from talking.commercial.client import ENTITLEMENT_VOICE_TRANSCRIPTION
from talking.languages import WHISPER_LANGUAGES
from talking.translate import TranslationUnavailable

from .groups import DEFAULT_MAX_MEMBERS, DEFAULT_TRIAL_MAX_MEMBERS, Group
from .models import Message
from .translation_cache import MessageTranslationCache

log = logging.getLogger(__name__)


def member_limit(settings, commercial, user_id: str) -> int:
    """GPL default 20; commercial trial/free fail-closed to the trial cap."""
    cap = max(2, int(getattr(settings, "chat_group_max_members", DEFAULT_MAX_MEMBERS) or DEFAULT_MAX_MEMBERS))
    trial = max(2, int(getattr(settings, "chat_group_trial_max_members", DEFAULT_TRIAL_MAX_MEMBERS)
                       or DEFAULT_TRIAL_MAX_MEMBERS))
    trial = min(trial, cap)
    if commercial is None or not getattr(commercial, "enabled", False):
        return cap
    decision = commercial.check_entitlement(user_id, ENTITLEMENT_VOICE_TRANSCRIPTION)
    tier = (decision.tier or "").lower()
    if decision.source == "dev_override" and decision.entitled:
        return cap
    if tier in ("paid", "premium"):
        return cap
    return trial


def viewer_lang(store, user_id: str, dialog_id: str, fallback: str = "en") -> str:
    if store.is_group(dialog_id):
        g = store.groups.get(dialog_id)
        m = g.member(user_id) if g else None
        if m and m.lang in WHISPER_LANGUAGES:
            return m.lang
    code = (fallback or "en").strip().lower()
    return code if code in WHISPER_LANGUAGES else "en"


def _cache(app) -> MessageTranslationCache:
    cache = getattr(app.state, "chat_translations", None)
    if cache is None:
        cache = MessageTranslationCache()
        app.state.chat_translations = cache
    return cache


def fill_translations(app, msg: Message, *, src_lang: str, members: list) -> Message:
    """Translate once per distinct member language. Cache hits skip the provider."""
    if msg.type != "text" or not (msg.text or "").strip():
        return msg
    src = (src_lang or "en").strip().lower()
    if src not in WHISPER_LANGUAGES:
        src = "en"
    msg.src_lang = src
    langs = sorted({
        (m.lang if getattr(m, "lang", None) in WHISPER_LANGUAGES else "en")
        for m in members
    })
    cache = _cache(app)
    translator = app.state.engine.translator
    stored = dict(msg.translations or {})
    if parse_command(msg.text):
        for lang in langs:
            stored[lang] = {"text": msg.text, "provider": "command_filter", "status": "skipped"}
            cache.store(msg.id, msg.text, src, lang, msg.text)
        msg.translations = stored
        return msg
    for lang in langs:
        hit = cache.get(msg.text, src, lang)
        if hit is not None:
            stored[lang] = {"text": hit, "provider": "cache", "status": "ok"}
            continue
        try:
            tr = translator.translate(msg.text, src, lang, force=True)
            text = tr.text or msg.text
            cache.store(msg.id, msg.text, src, lang, text)
            stored[lang] = {"text": text, "provider": tr.provider, "status": "ok"}
        except (TranslationUnavailable, Exception) as e:  # noqa: BLE001 — fallback to original
            log.info("group translation fallback lang=%s err=%s", lang, type(e).__name__)
            stored[lang] = {
                "text": msg.text,
                "provider": "fallback",
                "status": "fallback",
                "reason": str(e)[:200],
            }
            cache.store(msg.id, msg.text, src, lang, msg.text)
    msg.translations = stored
    return msg


def public_message(app, user_id: str, msg: Message) -> dict[str, Any]:
    play = None
    if msg.media_id:
        owner = user_id
        store = getattr(app.state, "chat_store", None)
        if store is not None and store.is_group(msg.dialog_id):
            owner = msg.sender_id or user_id
        tok = app.state.chat_tokens.mint(
            msg.media_id, owner, one_time=False, ttl=app.state.settings.chat_play_token_ttl,
        )
        play = f"/api/chat/media/{msg.media_id}?token={tok}"
    store = getattr(app.state, "chat_store", None)
    if store is not None and store.is_group(msg.dialog_id):
        fallback = getattr(app.state.engine.store, "get", lambda *_: "en")("target_lang") or "en"
        lang = viewer_lang(store, user_id, msg.dialog_id, fallback)
        return msg.overlay_for_lang(lang, play_url=play)
    return msg.to_public(play_url=play)


def publish_message(app, msg: Message, *, actor_id: str | None = None) -> None:
    hub = app.state.chat_hub
    store = getattr(app.state, "chat_store", None)
    if store is not None and store.is_group(msg.dialog_id):
        g = store.groups.get(msg.dialog_id)
        if g is None:
            return
        for member in g.members:
            public = public_message(app, member.user_id, msg)
            hub.publish(member.user_id, {"type": "chat.message", "message": public})
        return
    uid = actor_id or msg.sender_id
    hub.publish(uid, {"type": "chat.message", "message": public_message(app, uid, msg)})


def publish_deleted(app, dialog_id: str, message_id: str, *, actor_id: str) -> None:
    hub = app.state.chat_hub
    store = getattr(app.state, "chat_store", None)
    event = {"type": "chat.deleted", "message_id": message_id, "dialog_id": dialog_id}
    if store is not None and store.is_group(dialog_id):
        g = store.groups.get(dialog_id)
        if g is None:
            return
        for member in g.members:
            hub.publish(member.user_id, event)
        return
    hub.publish(actor_id, event)


def publish_group(app, group: Group, event_type: str, extra: dict | None = None) -> None:
    hub = app.state.chat_hub
    payload = {"type": event_type, "group_id": group.id, "group": group.to_preview()}
    if extra:
        payload.update(extra)
    for member in group.members:
        body = dict(payload)
        body["group"] = group.to_public(member.user_id)
        hub.publish(member.user_id, body)


def publish_transcript(app, msg: Message, *, actor_id: str) -> None:
    payload = {
        "type": "chat.transcript",
        "message_id": msg.id,
        "dialog_id": msg.dialog_id,
    }
    from .models import transcript_view
    view = transcript_view(msg)
    store = getattr(app.state, "chat_store", None)
    hub = app.state.chat_hub
    if store is not None and store.is_group(msg.dialog_id):
        g = store.groups.get(msg.dialog_id)
        if g is None:
            return
        for member in g.members:
            public = public_message(app, member.user_id, msg)
            hub.publish(member.user_id, {"type": "chat.message", "message": public})
            hub.publish(member.user_id, payload | {"status": view["status"], "transcript": view})
        return
    public = public_message(app, actor_id, msg)
    hub.publish(actor_id, {"type": "chat.message", "message": public})
    hub.publish(actor_id, payload | {"status": view["status"], "transcript": view})
