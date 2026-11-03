"""Press-and-hold voice translation: live conversation + record-for-translation.

Reuses faster-whisper (VAD-chunked windows / partial hypotheses) and the
existing Grok → Google → stub translator. Live audio is never persisted.
Record mode stores a ``voice_note`` only when the client opts into a chat
dialog.

See ``docs/VOICE_TRANSLATION.md``.
"""
from __future__ import annotations

from .entitlement import live_translate_allowed
from .pipeline import TranslateResult, resolve_direction, transcribe_and_translate
from .stream_stt import FakeStreamingSTT, Hypothesis, WindowedWhisperSTT

__all__ = [
    "FakeStreamingSTT",
    "Hypothesis",
    "TranslateResult",
    "WindowedWhisperSTT",
    "live_translate_allowed",
    "resolve_direction",
    "transcribe_and_translate",
]
