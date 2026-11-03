"""Minimal /api/chat/messages routes (link-preview + edits). M4 will extend."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request

from talking.auth.capabilities import deny_anonymous_capability
from talking.auth.deps import require_user

from ..security import TokenBucket, client_ip
from ..unfurl.service import UnfurlService
from ..unfurl.ssrf import SSRFError
from ..unfurl.urls import extract_urls
from .edits import EditError, first_url_changed
from .events import ChatHub, ensure_hub
from .messages import DEFAULT_CONVERSATION, MAX_TEXT, MessageStore
from .translation_cache import MessageTranslationCache


def _can_access(store: MessageStore, user_id: str, conversation_id: str) -> bool:
    """File-backed ChatStore is already namespaced by user. In-memory tests share a dict."""
    if getattr(store, "_chat", None) is not None:
        return True
    conv = conversation_id or DEFAULT_CONVERSATION
    if conv in (DEFAULT_CONVERSATION, user_id, "local"):
        return True
    msgs = store.list(conv, limit=store.max_per_conversation, user_id=user_id)
    if not msgs:
        return True
    return any(m.sender_id == user_id for m in msgs)


def _refresh_preview(unfurl: UnfurlService, text: str, old_urls: list[str], old_preview: dict | None):
    urls = extract_urls(text)
    if not first_url_changed(old_urls, urls):
        return urls, old_preview
    if not urls:
        return urls, None
    try:
        return urls, unfurl.preview(urls[0])
    except SSRFError:
        return urls, None


def register_message_routes(
    app: FastAPI,
    *,
    store: MessageStore,
    unfurl: UnfurlService,
    limiter: TokenBucket,
    edit_limiter: TokenBucket | None = None,
    hub: ChatHub | None = None,
    translations: MessageTranslationCache | None = None,
) -> None:
    hub = hub or ensure_hub(app)
    translations = translations or getattr(app.state, "chat_translations", None) or MessageTranslationCache()
    app.state.chat_translations = translations
    edit_limiter = edit_limiter or TokenBucket(20)

    def _window(request: Request) -> int:
        s = getattr(request.app.state, "settings", None)
        return int(getattr(s, "chat_edit_window_s", 48 * 3600) or 0)

    @app.get("/api/chat/messages")
    def list_messages(request: Request, conversation_id: str = DEFAULT_CONVERSATION, limit: int = 50):
        deny_anonymous_capability(request, "chat")
        uid = require_user(request).id
        conv = conversation_id or DEFAULT_CONVERSATION
        if not _can_access(store, uid, conv):
            raise HTTPException(403, "not a participant")
        return {
            "ok": True,
            "conversation_id": conv,
            "user_id": uid,
            "messages": [m.to_dict() for m in store.list(conv, limit, user_id=uid)],
            "edit_window_s": _window(request),
            "max_text": MAX_TEXT,
        }

    @app.post("/api/chat/messages")
    async def post_message(request: Request):
        deny_anonymous_capability(request, "chat")
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "rate limit")
        uid = require_user(request).id
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        text = str(data.get("text") or "")
        if not text.strip():
            raise HTTPException(400, "empty")
        if len(text) > MAX_TEXT:
            raise HTTPException(400, "text too long")
        claimed = data.get("sender_id")
        if claimed is not None and str(claimed) and str(claimed) != uid:
            raise HTTPException(403, "sender_id spoofing")
        sender = uid
        conv = str(data.get("conversation_id") or DEFAULT_CONVERSATION)[:64]
        urls = extract_urls(text)
        preview = None
        if urls:
            try:
                preview = unfurl.preview(urls[0])
            except SSRFError:
                preview = None
        msg = store.add(
            text=text,
            sender_id=sender,
            conversation_id=conv,
            urls=urls,
            preview=preview,
            user_id=uid,
        )
        return {"ok": True, "message": msg.to_dict()}

    @app.patch("/api/chat/messages/{message_id}")
    async def patch_message(message_id: str, request: Request):
        deny_anonymous_capability(request, "chat")
        if not edit_limiter.allow(client_ip(request)):
            raise HTTPException(429, "rate limit")
        uid = require_user(request).id
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        if "text" not in data:
            raise HTTPException(422, "text required")
        claimed = data.get("sender_id")
        if claimed is not None and str(claimed) and str(claimed) != uid:
            raise HTTPException(403, "sender_id spoofing")
        sender = uid
        conv = data.get("conversation_id")
        conv_s = str(conv)[:64] if conv else None
        old = store.get(message_id, conv_s, user_id=uid)
        if old is None:
            raise HTTPException(404, "message not found")
        if old.sender_id != uid:
            raise HTTPException(403, "not the author")
        old_text = old.text
        old_urls = list(old.urls)
        old_preview = old.preview
        try:
            msg, changed = store.edit_text(
                message_id,
                data.get("text"),
                actor_id=sender,
                now=time.time(),
                window_s=_window(request),
                max_text=MAX_TEXT,
                conversation_id=conv_s,
                user_id=uid,
            )
        except EditError as e:
            raise HTTPException(e.status, e.message) from e
        if changed:
            translations.invalidate_message(msg.id, old_text=old_text)
            urls, preview = _refresh_preview(unfurl, msg.text, old_urls, old_preview)
            msg.urls = urls
            msg.preview = preview
            public = msg.to_dict()
            hub.publish_edit(public)
            return {"ok": True, "message": public, "changed": True}
        return {"ok": True, "message": msg.to_dict(), "changed": False}
