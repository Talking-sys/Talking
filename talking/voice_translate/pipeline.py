"""Shared STT + translation used by live WS, record HTTP, and chat notes.

The premium on-demand note transcription PR should call ``transcribe_and_translate``
rather than loading Whisper a second time. ``attach_transcript`` in
``talking.chat.notes`` stays skip-if-not-ready so uploads never pull large-v3.
"""
from __future__ import annotations

from dataclasses import dataclass

from talking.languages import WHISPER_LANGUAGES, display_name
from talking.stt import STTResult, STTUnavailable
from talking.translate import TranslationResult, TranslationUnavailable


@dataclass
class Direction:
    src: str
    tgt: str
    speaker: str  # "a" (source side) or "b" (target side speaking)


@dataclass
class TranslateResult:
    text: str
    translation: str
    src_lang: str
    tgt_lang: str
    speaker: str
    provider: str
    stt_ms: int
    mt_ms: int
    lang_prob: float
    partial: bool = False

    def to_public(self) -> dict:
        return {
            "text": self.text,
            "translation": self.translation,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "speaker": self.speaker,
            "provider": self.provider,
            "stt_ms": self.stt_ms,
            "mt_ms": self.mt_ms,
            "lang_prob": round(self.lang_prob, 3),
            "partial": self.partial,
            "src_name": display_name(self.src_lang),
            "tgt_name": display_name(self.tgt_lang),
        }


def normalize_lang(code: str | None, *, allow_auto: bool = False) -> str | None:
    if code is None:
        return None
    c = str(code).strip().lower()
    if allow_auto and c in ("", "auto"):
        return "auto"
    if c in WHISPER_LANGUAGES:
        return c
    return None


def resolve_direction(
    detected: str,
    source_lang: str,
    target_lang: str,
    partner_lang: str,
) -> Direction:
    """Pick src→tgt and which conversation side is speaking.

    ``source_lang="auto"``: Whisper's detected code is the source. If that code
    is the *target* picker, the other person is talking and we translate back
    into ``partner_lang`` (Google Translate conversation behaviour).
    """
    detected = (detected or "en").strip().lower() or "en"
    if detected not in WHISPER_LANGUAGES:
        detected = "en"
    target = target_lang if target_lang in WHISPER_LANGUAGES else "en"
    partner = partner_lang if partner_lang in WHISPER_LANGUAGES else ("ru" if target != "ru" else "en")
    if partner == target:
        partner = "ru" if target != "ru" else "en"

    src_fixed = source_lang if source_lang in WHISPER_LANGUAGES else None

    if detected == target:
        return Direction(src=detected, tgt=partner if src_fixed is None else src_fixed, speaker="b")
    if src_fixed and detected != src_fixed and detected != target:
        # Forced source, but Whisper heard something else that isn't the target:
        # still honour the picker so a noisy detection does not flip the pair.
        return Direction(src=src_fixed, tgt=target, speaker="a")
    return Direction(src=detected, tgt=target, speaker="a")


def swap_pair(source_lang: str, target_lang: str, partner_lang: str) -> tuple[str, str, str]:
    """Swap A↔B. Auto-detect stays on the source picker."""
    target = target_lang if target_lang in WHISPER_LANGUAGES else "en"
    partner = partner_lang if partner_lang in WHISPER_LANGUAGES else ("ru" if target != "ru" else "en")
    if source_lang == "auto" or source_lang not in WHISPER_LANGUAGES:
        return "auto", partner, target
    return target, source_lang, source_lang


def transcribe_and_translate(
    engine,
    audio,
    *,
    source_lang: str = "auto",
    target_lang: str = "en",
    partner_lang: str = "ru",
    load_stt: bool = True,
    partial: bool = False,
) -> TranslateResult:
    """Run existing ``engine.stt`` + ``engine.translator``. Never invents a second stack.

    ``load_stt=False`` matches chat-note behaviour (skip if Whisper is not already
    loaded). Record-for-translation uses the default so FakeSTT/production STT run.
    """
    stt = getattr(engine, "stt", None)
    if stt is None:
        raise STTUnavailable("STT is not configured")
    if not load_stt and not getattr(stt, "ready", False):
        raise STTUnavailable("STT is not loaded; transcript not attached")
    hint = None if source_lang in (None, "", "auto") else source_lang
    result: STTResult = stt.transcribe(audio, language=hint)
    text = (result.text or "").strip()
    if not text:
        direction = resolve_direction(result.lang or "en", source_lang, target_lang, partner_lang)
        return TranslateResult(
            text="", translation="", src_lang=direction.src, tgt_lang=direction.tgt,
            speaker=direction.speaker, provider="none", stt_ms=result.elapsed_ms,
            mt_ms=0, lang_prob=result.lang_prob, partial=partial,
        )
    direction = resolve_direction(result.lang or "en", source_lang, target_lang, partner_lang)
    try:
        tr: TranslationResult = engine.translator.translate(text, direction.src, direction.tgt)
        translation, provider, mt_ms = tr.text, tr.provider, tr.elapsed_ms
    except TranslationUnavailable:
        translation, provider, mt_ms = text, "original", 0
    return TranslateResult(
        text=text, translation=translation, src_lang=direction.src, tgt_lang=direction.tgt,
        speaker=direction.speaker, provider=provider, stt_ms=result.elapsed_ms,
        mt_ms=mt_ms, lang_prob=result.lang_prob, partial=partial,
    )
