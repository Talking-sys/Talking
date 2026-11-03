"""Dialog + message records for the notes slice and M4 1:1 chat.

One message model for notes, link previews, edits, and transcription.
Canonical discriminator is ``kind``; ``type`` is an alias. Canonical
conversation key is ``dialog_id``; ``conversation_id`` is the alias used by
``/api/chat/messages``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .edits import public_edit_fields

MESSAGE_TYPES = ("text", "voice_note", "video_note")
NOTE_TYPES = ("voice_note", "video_note")
_ALIAS_KEYS = (
    "play_url", "kind", "type", "conversation_id", "dialog_id", "edited",
    "unread_count", "peer", "last_message",
)


@dataclass
class Dialog:
    id: str
    title: str
    created_at: float
    updated_at: float
    kind: str = "local"  # local | direct
    participants: list[str] = field(default_factory=list)
    last_message_id: str | None = None
    last_sender_id: str | None = None
    last_preview: str = ""
    last_type: str | None = None
    prefs: dict[str, Any] = field(default_factory=dict)

    def to_public(self, *, viewer_id: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        d = {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "kind": self.kind,
        }
        if self.kind == "direct":
            d["participants"] = list(self.participants)
        if extra:
            d.update(extra)
        if viewer_id and self.kind == "direct":
            d.setdefault("peer_id", next((p for p in self.participants if p != viewer_id), None))
        return d

    def to_storage(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "kind": self.kind,
            "participants": list(self.participants),
            "last_message_id": self.last_message_id,
            "last_sender_id": self.last_sender_id,
            "last_preview": self.last_preview,
            "last_type": self.last_type,
            "prefs": dict(self.prefs or {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Dialog":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kw = {k: v for k, v in d.items() if k in known}
        kw.setdefault("kind", "local")
        kw.setdefault("participants", list(d.get("participants") or []))
        kw.setdefault("prefs", dict(d.get("prefs") or {}))
        return cls(**kw)


@dataclass
class Message:
    id: str
    dialog_id: str
    sender_id: str
    type: str
    created_at: float
    text: str = ""
    duration_ms: int | None = None
    media_id: str | None = None
    mime: str | None = None
    size: int | None = None
    waveform: list[int] | None = None
    transcript: str | None = None
    translation: str | None = None
    src_lang: str | None = None
    tgt_lang: str | None = None
    translation_provider: str | None = None
    transcript_status: str | None = None
    transcript_reason: str | None = None
    transcript_provider: str | None = None
    transcript_model: str | None = None
    transcript_created_at: float | None = None
    transcript_duration: float | None = None
    urls: list[str] = field(default_factory=list)
    preview: dict | None = None
    edited_at: float | None = None
    edit_count: int = 0
    # Server-only: persist in JSONL, never in to_public() / to_dict().
    original_text: str | None = None
    revisions: list[dict] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    # Per (message, target_lang) cache persisted on the row.
    translations: dict[str, Any] = field(default_factory=dict)
    delivered_at: float | None = None
    read_at: float | None = None
    deleted_at: float | None = None

    @property
    def kind(self) -> str:
        return self.type

    @kind.setter
    def kind(self, value: str) -> None:
        self.type = value or "text"

    @property
    def conversation_id(self) -> str:
        return self.dialog_id

    @conversation_id.setter
    def conversation_id(self, value: str) -> None:
        self.dialog_id = value or self.dialog_id

    def to_public(self, play_url: str | None = None, *, viewer_id: str | None = None) -> dict[str, Any]:
        d = {
            "id": self.id,
            "dialog_id": self.dialog_id,
            "conversation_id": self.dialog_id,
            "sender_id": self.sender_id,
            "type": self.type,
            "kind": self.type,
            "created_at": self.created_at,
            "text": self.text,
            "duration_ms": self.duration_ms,
            "media_id": self.media_id,
            "mime": self.mime,
            "size": self.size,
            "waveform": self.waveform,
            "transcript": self.transcript,
            "translation": self.translation,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "translation_provider": self.translation_provider,
            "transcript_status": self.transcript_status,
            "transcript_reason": self.transcript_reason,
            "transcript_provider": self.transcript_provider,
            "transcript_model": self.transcript_model,
            "transcript_created_at": self.transcript_created_at,
            "transcript_duration": self.transcript_duration,
            "urls": list(self.urls or []),
            "preview": self.preview,
        }
        d.update(public_edit_fields(self))
        if play_url:
            d["play_url"] = play_url
        if self.delivered_at:
            d["delivered_at"] = self.delivered_at
        if self.read_at:
            d["read_at"] = self.read_at
        if viewer_id and viewer_id == self.sender_id:
            if self.read_at:
                d["receipt"] = "read"
            elif self.delivered_at:
                d["receipt"] = "delivered"
            else:
                d["receipt"] = "sent"
        return d

    def to_dict(self) -> dict[str, Any]:
        """Wire shape for ``/api/chat/messages`` (never includes edit history)."""
        return self.to_public()

    def to_storage(self) -> dict[str, Any]:
        """JSONL row: public fields plus private edit history, no play_url."""
        d = self.to_public()
        d.pop("play_url", None)
        d.pop("receipt", None)
        d["original_text"] = self.original_text
        d["revisions"] = list(self.revisions or [])
        d["translations"] = dict(self.translations or {})
        d["delivered_at"] = self.delivered_at
        d["read_at"] = self.read_at
        d["deleted_at"] = self.deleted_at
        return d

    def to_admin(self) -> dict[str, Any]:
        """Admin views must never see note transcripts (owner spec / M11)."""
        return redact_for_admin(self.to_public())

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        data = dict(d)
        if not data.get("type") and data.get("kind"):
            data["type"] = data["kind"]
        if not data.get("dialog_id") and data.get("conversation_id"):
            data["dialog_id"] = data["conversation_id"]
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kw = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known and k not in _ALIAS_KEYS}
        kw.setdefault("extra", extra)
        kw.setdefault("translations", dict(data.get("translations") or extra.pop("translations", {}) or {}))
        return cls(**kw)


ADMIN_REDACT_FIELDS = (
    "transcript", "translation", "src_lang", "tgt_lang", "translation_provider",
    "transcript_status", "transcript_reason", "transcript_provider",
    "transcript_model", "transcript_created_at", "transcript_duration",
    "text", "original_text", "translations", "preview", "urls",
)


def redact_for_admin(public: dict[str, Any]) -> dict[str, Any]:
    """Strip speech-to-text fields so an admin panel cannot read note text."""
    out = {k: v for k, v in public.items() if k not in ADMIN_REDACT_FIELDS}
    out["transcript_redacted"] = True
    out["content_redacted"] = True
    return out


def transcript_view(msg: Message) -> dict[str, Any]:
    status = msg.transcript_status
    if status in ("ok",):
        status = "done"
    elif status in ("error", "skipped"):
        status = "failed"
    return {
        "message_id": msg.id,
        "status": status,
        "transcript": msg.transcript,
        "translation": msg.translation,
        "language": msg.src_lang,
        "target_lang": msg.tgt_lang,
        "provider": msg.transcript_provider,
        "model": msg.transcript_model,
        "created_at": msg.transcript_created_at,
        "duration": msg.transcript_duration,
        "reason": msg.transcript_reason,
    }


def preview_text(msg: Message, *, limit: int = 80) -> str:
    if msg.type == "voice_note":
        return "Voice note"
    if msg.type == "video_note":
        return "Video note"
    body = (msg.text or "").replace("\n", " ").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 1] + "…"
