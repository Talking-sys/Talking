"""Response models for the Talking game SDK."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    code: str
    en: str
    ru: str = ""
    label: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Language":
        code = str(d.get("code") or "")
        en = str(d.get("en") or code)
        ru = str(d.get("ru") or "")
        label = str(d.get("label") or en)
        return cls(code=code, en=en, ru=ru, label=label)


@dataclass
class Translation:
    text: str
    translation: str
    source: str
    target: str
    provider: str = ""
    request_id: str = ""
    elapsed_ms: int = 0
    stt_ms: int | None = None
    tts_ms: int | None = None
    audio: bytes | None = None
    audio_mime: str | None = None
    audio_error: str | None = None
    lang_prob: float | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def translated_text(self) -> str:
        return self.translation

    @classmethod
    def from_dict(cls, d: dict) -> "Translation":
        audio_b64 = d.get("audio")
        audio = None
        if isinstance(audio_b64, str) and audio_b64:
            import base64
            try:
                audio = base64.b64decode(audio_b64)
            except Exception:
                audio = None
        lp = d.get("lang_prob")
        try:
            lp_f = float(lp) if lp is not None else None
        except (TypeError, ValueError):
            lp_f = None
        return cls(
            text=str(d.get("text") or ""),
            translation=str(d.get("translation") or ""),
            source=str(d.get("source") or ""),
            target=str(d.get("target") or ""),
            provider=str(d.get("provider") or ""),
            request_id=str(d.get("request_id") or ""),
            elapsed_ms=int(d.get("elapsed_ms") or 0),
            stt_ms=int(d["stt_ms"]) if d.get("stt_ms") is not None else None,
            tts_ms=int(d["tts_ms"]) if d.get("tts_ms") is not None else None,
            audio=audio,
            audio_mime=str(d["audio_mime"]) if d.get("audio_mime") else None,
            audio_error=str(d["audio_error"]) if d.get("audio_error") else None,
            lang_prob=lp_f,
            raw=d,
        )


@dataclass(frozen=True)
class StreamEvent:
    event: str
    data: dict

    @property
    def translation(self) -> Translation | None:
        if self.event == "translation" and isinstance(self.data, dict):
            return Translation.from_dict(self.data)
        return None
