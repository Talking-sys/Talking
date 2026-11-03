"""Pluggable streaming STT: local faster-whisper windows, fake for tests.

faster-whisper is utterance-oriented. Live mode approximates streaming by:

1. Accumulating PCM while the speaker is active.
2. Every ``partial_interval_s``, re-decoding a trailing window (partial hypothesis).
3. On VAD / client ``eou``, decoding the full utterance as final.

Latency (CPU, ``base`` / ``small``): partials typically 200–800 ms after the
interval; ``large-v3`` is 1–3 s per window. No audio leaves the box except the
translated *text* sent to Grok/Google. A future cloud streaming STT (Deepgram,
xAI realtime, …) should implement the same ``StreamingSTT`` surface — feed/flush/
reset — without changing the WebSocket event schema.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np

from talking.stt import STTResult


@dataclass
class Hypothesis:
    text: str
    lang: str
    lang_prob: float = 0.99
    partial: bool = False
    elapsed_ms: int = 1
    duration: float = 0.0


class StreamingSTT(Protocol):
    def feed(self, pcm: np.ndarray, language: str | None = None) -> list[Hypothesis]: ...
    def flush(self, language: str | None = None) -> list[Hypothesis]: ...
    def reset(self) -> None: ...


class WindowedWhisperSTT:
    """VAD-chunked overlapping windows over an existing ``STTEngine`` / FakeSTT."""

    def __init__(
        self,
        stt,
        sample_rate: int = 16000,
        partial_interval_s: float = 0.8,
        partial_window_s: float = 3.0,
        min_s: float = 0.35,
        max_s: float = 18.0,
        clock=None,
    ):
        self.stt = stt
        self.sr = sample_rate
        self.partial_interval_s = partial_interval_s
        self.partial_window_s = partial_window_s
        self.min_samples = max(1, int(min_s * sample_rate))
        self.max_samples = max(self.min_samples, int(max_s * sample_rate))
        self._clock = clock or __import__("time").monotonic
        self._chunks: list[np.ndarray] = []
        self._n = 0
        self._last_partial_at = 0.0
        self._last_partial_text = ""

    def reset(self) -> None:
        self._chunks = []
        self._n = 0
        self._last_partial_at = 0.0
        self._last_partial_text = ""

    def _concat(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        if len(self._chunks) == 1:
            return self._chunks[0]
        return np.concatenate(self._chunks)

    def _transcribe(self, audio: np.ndarray, language: str | None, partial: bool) -> Hypothesis | None:
        if audio is None or len(audio) < self.min_samples:
            return None
        result: STTResult = self.stt.transcribe(audio, language=language)
        text = (result.text or "").strip()
        if not text:
            return None
        if partial and text == self._last_partial_text:
            return None
        if partial:
            self._last_partial_text = text
        return Hypothesis(
            text=text, lang=result.lang, lang_prob=result.lang_prob,
            partial=partial, elapsed_ms=result.elapsed_ms, duration=result.duration,
        )

    def feed(self, pcm: np.ndarray, language: str | None = None) -> list[Hypothesis]:
        if pcm is None or len(pcm) == 0:
            return []
        samples = np.ascontiguousarray(pcm, dtype=np.float32)
        self._chunks.append(samples)
        self._n += len(samples)
        out: list[Hypothesis] = []
        if self._n >= self.max_samples:
            out.extend(self.flush(language=language))
            return out
        now = self._clock()
        if self._n >= self.min_samples and (now - self._last_partial_at) >= self.partial_interval_s:
            audio = self._concat()
            win = int(self.partial_window_s * self.sr)
            if len(audio) > win:
                audio = audio[-win:]
            hyp = self._transcribe(audio, language, partial=True)
            self._last_partial_at = now
            if hyp is not None:
                out.append(hyp)
        return out

    def flush(self, language: str | None = None) -> list[Hypothesis]:
        audio = self._concat()
        self.reset()
        hyp = self._transcribe(audio, language, partial=False)
        return [hyp] if hyp is not None else []


class FakeStreamingSTT:
    """Programmed partial/final hypotheses for WebSocket tests. No Whisper."""

    def __init__(self, events: list[Hypothesis] | None = None):
        self.queue: list[Hypothesis] = list(events or [])
        self.calls: list[tuple] = []
        self.fed_samples = 0

    def push(self, text: str, lang: str = "en", *, partial: bool = False, lang_prob: float = 0.99) -> None:
        self.queue.append(Hypothesis(text=text, lang=lang, lang_prob=lang_prob, partial=partial))

    def reset(self) -> None:
        self.fed_samples = 0

    def feed(self, pcm: np.ndarray, language: str | None = None) -> list[Hypothesis]:
        n = 0 if pcm is None else int(len(pcm))
        self.fed_samples += n
        self.calls.append(("feed", n, language))
        if n <= 0:
            return []
        if self.queue and self.queue[0].partial:
            h = self.queue.pop(0)
            if language:
                h = replace(h, lang=language)
            return [h]
        return []

    def flush(self, language: str | None = None) -> list[Hypothesis]:
        self.calls.append(("flush", language))
        out: list[Hypothesis] = []
        while self.queue:
            h = self.queue.pop(0)
            if language:
                h = replace(h, lang=language)
            out.append(h)
            if not h.partial:
                break
        return out
