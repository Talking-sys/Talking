"""Dialog + message records for the notes slice.

M4 can add members, nicks, groups, auto-delete, etc. without rewriting callers
that already consume ``to_public()``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MESSAGE_TYPES = ("text", "voice_note", "video_note")
NOTE_TYPES = ("voice_note", "video_note")


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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_public(self, play_url: str | None = None) -> dict[str, Any]:
        d = {
            "id": self.id,
            "dialog_id": self.dialog_id,
            "sender_id": self.sender_id,
            "type": self.type,
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
        }
        if play_url:
            d["play_url"] = play_url
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kw = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known and k != "play_url"}
        kw.setdefault("extra", extra)
        return cls(**kw)
