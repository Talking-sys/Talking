"""Minimal /api/chat/messages routes (link-preview only). M4 will extend."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from ..security import TokenBucket, client_ip
from ..unfurl.service import UnfurlService
from ..unfurl.ssrf import SSRFError
from ..unfurl.urls import extract_urls
from .messages import DEFAULT_CONVERSATION, MAX_TEXT, MessageStore


def register_message_routes(
    app: FastAPI,
    *,
    store: MessageStore,
    unfurl: UnfurlService,
    limiter: TokenBucket,
) -> None:
    @app.get("/api/chat/messages")
    def list_messages(conversation_id: str = DEFAULT_CONVERSATION, limit: int = 50):
        return {
            "ok": True,
            "conversation_id": conversation_id or DEFAULT_CONVERSATION,
            "messages": [m.to_dict() for m in store.list(conversation_id, limit)],
        }

    @app.post("/api/chat/messages")
    async def post_message(request: Request):
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "rate limit")
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
        sender = str(data.get("sender_id") or "local")[:64]
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
        )
        return {"ok": True, "message": msg.to_dict()}
