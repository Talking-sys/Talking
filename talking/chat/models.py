"""Dialog + message records for the notes slice.

One message model for notes, link previews, and edits. Canonical discriminator
is ``kind``; ``type`` is kept as an alias so PR #3 clients/tests keep working.
Canonical conversation key is ``dialog_id``; ``conversation_id`` is the alias
used by ``/api/chat/messages``. See the merge commit that unified PRs #3/#5/#6.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .edits import public_edit_fields

MESSAGE_TYPES = ("text", "voice_note", "video_note")
NOTE_TYPES = ("voice_note", "video_note")
_ALIAS_KEYS = ("play_url", "kind", "type", "conversation_id", "dialog_id", "edited")


@dataclass
class Dialog:
    id: str
    title: str
    created_at: float
    updated_at: float

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


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
    urls: list[str] = field(default_factory=list)
    preview: dict | None = None
    edited_at: float | None = None
    edit_count: int = 0
    # Server-only: persist in JSONL, never in to_public() / to_dict().
    original_text: str | None = None
    revisions: list[dict] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

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

    def to_public(self, play_url: str | None = None) -> dict[str, Any]:
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
            "urls": list(self.urls or []),
            "preview": self.preview,
        }
        d.update(public_edit_fields(self))
        if play_url:
            d["play_url"] = play_url
        return d

    def to_dict(self) -> dict[str, Any]:
        """Wire shape for ``/api/chat/messages`` (never includes edit history)."""
        return self.to_public()

    def to_storage(self) -> dict[str, Any]:
        """JSONL row: public fields plus private edit history, no play_url."""
        d = self.to_public()
        d.pop("play_url", None)
        d["original_text"] = self.original_text
        d["revisions"] = list(self.revisions or [])
        return d

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
        return cls(**kw)
