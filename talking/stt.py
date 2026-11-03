"""Speech-to-text: faster-whisper with auto language detection.

Default model is large-v3; the UI can switch to base / small / medium.
Tests inject FakeSTTEngine so CI never downloads weights.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .config import DEFAULT_WHISPER_MODEL, WHISPER_MODELS, Settings

log = logging.getLogger(__name__)


class STTUnavailable(RuntimeError):
    pass


@dataclass
class STTResult:
    text: str
    lang: str
    lang_prob: float
    duration: float
    elapsed_ms: int


class STTEngine:
    def __init__(self, settings: Settings):
        self.s = settings
        self._model = None
        self._lock = threading.Lock()
        self.error: str | None = None
        self.device = None
        self.compute_type = None
        self.loaded_name: str | None = None

    def _resolve_device(self) -> tuple[str, str]:
        device = self.s.whisper_device
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        ct = self.s.whisper_compute_type
        if ct == "auto":
            ct = "float16" if device == "cuda" else "int8"
        return device, ct

    def load(self, model_name: str | None = None):
        name = model_name or self.s.normalized_whisper_model()
        if name not in WHISPER_MODELS:
            name = DEFAULT_WHISPER_MODEL
        if self._model is not None and self.loaded_name == name:
            return self._model
        with self._lock:
            if self._model is not None and self.loaded_name == name:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                self.error = "faster-whisper is not installed: pip install faster-whisper"
                raise STTUnavailable(self.error) from e
            self.device, self.compute_type = self._resolve_device()
            threads = self.s.whisper_cpu_threads or max(1, (os.cpu_count() or 2) - 1)
            t0 = time.time()
            log.info("Loading Whisper '%s' (%s, %s, threads=%d)…", name, self.device, self.compute_type, threads)
            try:
                self._model = WhisperModel(name, device=self.device,
                                           compute_type=self.compute_type, cpu_threads=threads)
            except Exception as e:
                self.error = (
                    f"Failed to load Whisper model '{name}': {e}. "
                    "Check network (first download) or pick a smaller model (WHISPER_MODEL=base/small)."
                )
                raise STTUnavailable(self.error) from e
            self.loaded_name = name
            self.s.whisper_model = name
            log.info("Whisper loaded in %.1f s", time.time() - t0)
            self.error = None
            return self._model

    @property
    def ready(self) -> bool:
        return self._model is not None

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> STTResult:
        model = self.load()
        t0 = time.time()
        with self._lock:
            segments, info = model.transcribe(
                audio, language=language, beam_size=self.s.whisper_beam_size,
                vad_filter=self.s.whisper_vad_filter, condition_on_previous_text=False,
                without_timestamps=True)
            text = " ".join(s.text.strip() for s in segments).strip()
        return STTResult(text=text, lang=info.language, lang_prob=float(info.language_probability or 0.0),
                         duration=len(audio) / self.s.sample_rate,
                         elapsed_ms=int((time.time() - t0) * 1000))


class FakeSTTEngine:
    """Deterministic STT for tests and keyless CI. Does not load Whisper."""

    def __init__(self, settings: Settings | None = None, results: list[STTResult] | None = None,
                 default_text: str = "hello", default_lang: str = "en"):
        self.s = settings
        self._lock = threading.Lock()
        self.error: str | None = None
        self.device = "cpu"
        self.compute_type = "fake"
        self.loaded_name = "fake"
        self.default_text = default_text
        self.default_lang = default_lang
        self.queue: list[STTResult] = list(results or [])
        self.calls: list[STTResult] = []

    def load(self, model_name: str | None = None):
        if model_name:
            self.loaded_name = model_name
        return self

    @property
    def ready(self) -> bool:
        return True

    def push(self, text: str, lang: str = "en", lang_prob: float = 0.99) -> None:
        self.queue.append(STTResult(text, lang, lang_prob, 1.0, 1))

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> STTResult:
        dur = float(len(audio) / (self.s.sample_rate if self.s else 16000)) if audio is not None else 0.0
        with self._lock:
            if self.queue:
                r = self.queue.pop(0)
                r = STTResult(r.text, language or r.lang, r.lang_prob, dur or r.duration, 1)
            else:
                r = STTResult(self.default_text, language or self.default_lang, 0.99, dur, 1)
            self.calls.append(r)
            return r
