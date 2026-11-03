"""In-memory live translation session. Audio is discarded after each utterance."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from talking.ids import check_id
from talking.languages import WHISPER_LANGUAGES, display_name
from talking.stt import STTUnavailable
from talking.translate import TranslationUnavailable

from .pipeline import normalize_lang, resolve_direction, swap_pair
from .stream_stt import Hypothesis, WindowedWhisperSTT

Emit = Callable[[dict], None]


class LiveTranslateSession:
    def __init__(
        self,
        engine,
        emit: Emit,
        user_id: str | None = None,
        *,
        stt=None,
        clock=None,
        max_s: float | None = None,
        max_pending_s: float | None = None,
    ):
        self.engine = engine
        self.emit = emit
        self._clock = clock or time.time
        s = engine.s
        self.id = "lt-" + uuid.uuid4().hex[:10]
        raw_uid = user_id or engine.store.get("user_id") or "local"
        try:
            self.user_id = check_id(raw_uid)
        except ValueError:
            self.user_id = "local"
        self.source_lang = "auto"
        self.target_lang = engine.store.get("target_lang") or s.lang_b or "en"
        self.partner_lang = engine.store.get("partner_lang") or s.lang_a or "ru"
        self.tts = True
        self.client_vad = True
        self.started = False
        self.closed = False
        self.started_at = 0.0
        self.max_s = float(s.voice_translate_live_max_s if max_s is None else max_s)
        self.max_pending_s = float(
            s.voice_translate_max_pending_audio_s if max_pending_s is None else max_pending_s
        )
        self.stt = stt or WindowedWhisperSTT(
            engine.stt,
            sample_rate=s.sample_rate,
            partial_interval_s=s.voice_translate_partial_interval_s,
            partial_window_s=s.voice_translate_partial_window_s,
            min_s=s.min_segment_s,
            max_s=s.max_segment_s,
            clock=self._clock,
        )
        self._pcm: list[np.ndarray] = []
        self._pcm_n = 0
        self._lock = threading.Lock()
        self._pending_job = False
        self._flush_requested = False
        self._dropped_frames = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"live-{self.id}")
        self._utt_id: str | None = None

    def state(self) -> dict:
        return {
            "type": "config",
            "session_id": self.id,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "partner_lang": self.partner_lang,
            "tts": self.tts,
            "client_vad": self.client_vad,
            "max_s": self.max_s,
            "partial_interval_s": getattr(self.engine.s, "voice_translate_partial_interval_s", 0.8),
        }

    def configure(self, **kw) -> dict:
        src = normalize_lang(kw.get("source_lang"), allow_auto=True)
        if src:
            self.source_lang = src
        tgt = normalize_lang(kw.get("target_lang"))
        if tgt:
            self.target_lang = tgt
        partner = normalize_lang(kw.get("partner_lang"))
        if partner:
            self.partner_lang = partner
        if "tts" in kw:
            self.tts = bool(kw["tts"])
        if "client_vad" in kw:
            self.client_vad = bool(kw["client_vad"])
        return self.state()

    def swap(self) -> dict:
        self.source_lang, self.target_lang, self.partner_lang = swap_pair(
            self.source_lang, self.target_lang, self.partner_lang,
        )
        ev = self.state()
        ev["type"] = "swap"
        return ev

    def start(self) -> dict:
        self.started = True
        self.started_at = self._clock()
        self._dropped_frames = 0
        try:
            self.stt.reset()
        except Exception:
            pass
        return {"type": "started", "session_id": self.id, "max_s": self.max_s}

    def stop(self) -> dict:
        self.end_of_utterance()
        self.started = False
        return {"type": "stopped", "session_id": self.id}

    def _hint(self) -> str | None:
        return None if self.source_lang == "auto" else self.source_lang

    def _pending_s(self) -> float:
        return self._pcm_n / float(self.engine.s.sample_rate or 16000)

    def _over_limit(self) -> bool:
        if self.max_s <= 0 or not self.started:
            return False
        return (self._clock() - self.started_at) >= self.max_s

    def _limit(self) -> None:
        if not self.started:
            return
        self.started = False
        self.emit({
            "type": "limit",
            "reason": "max_session_s",
            "max_s": self.max_s,
            "session_id": self.id,
        })
        self.end_of_utterance()

    def feed_pcm(self, samples: np.ndarray) -> None:
        if self.closed or not self.started:
            return
        if self._over_limit():
            self._limit()
            return
        pcm = np.ascontiguousarray(samples, dtype=np.float32)
        with self._lock:
            if self._pending_s() + len(pcm) / float(self.engine.s.sample_rate or 16000) > self.max_pending_s:
                self._dropped_frames += 1
                self.emit({
                    "type": "backpressure",
                    "dropped_frames": self._dropped_frames,
                    "pending_s": round(self._pending_s(), 3),
                })
                return
            self._pcm.append(pcm)
            self._pcm_n += len(pcm)
            need = not self._pending_job
            if need:
                self._pending_job = True
        if need:
            self._pool.submit(self._drain)

    def end_of_utterance(self) -> None:
        if self.closed:
            return
        with self._lock:
            self._flush_requested = True
            need = not self._pending_job
            if need:
                self._pending_job = True
        if need:
            self._pool.submit(self._drain)

    def _drain(self) -> None:
        try:
            while True:
                with self._lock:
                    chunk = None
                    if self._pcm:
                        chunk = self._pcm[0] if len(self._pcm) == 1 else np.concatenate(self._pcm)
                        self._pcm, self._pcm_n = [], 0
                    flush = self._flush_requested
                    if flush:
                        self._flush_requested = False
                    if chunk is None and not flush:
                        self._pending_job = False
                        return
                if chunk is not None and len(chunk):
                    try:
                        hyps = self.stt.feed(chunk, language=self._hint())
                    except STTUnavailable as e:
                        self.emit({"type": "notice", "level": "error", "text": str(e)})
                        hyps = []
                    except Exception as e:
                        self.emit({"type": "notice", "level": "error", "text": f"STT error: {e}"})
                        hyps = []
                    for h in hyps:
                        self._handle(h)
                if flush:
                    try:
                        hyps = self.stt.flush(language=self._hint())
                    except Exception as e:
                        self.emit({"type": "notice", "level": "error", "text": f"STT error: {e}"})
                        hyps = []
                    for h in hyps:
                        self._handle(h)
                    self._utt_id = None
        except Exception as e:
            self.emit({"type": "notice", "level": "error", "text": f"Live session error: {e}"})
            with self._lock:
                self._pending_job = False

    def _handle(self, hyp: Hypothesis) -> None:
        if not hyp or not (hyp.text or "").strip():
            return
        text = hyp.text.strip()
        detected = hyp.lang if hyp.lang in WHISPER_LANGUAGES else (hyp.lang or "en")
        direction = resolve_direction(detected, self.source_lang, self.target_lang, self.partner_lang)
        if self.source_lang == "auto" and direction.speaker == "a" and direction.src in WHISPER_LANGUAGES:
            # Remember the other side so a later target-language utterance can swap back.
            if direction.src != self.target_lang:
                self.partner_lang = direction.src
        translation = ""
        provider = "none"
        mt_ms = 0
        try:
            tr = self.engine.translator.translate(text, direction.src, direction.tgt)
            translation, provider, mt_ms = tr.text, tr.provider, tr.elapsed_ms
        except TranslationUnavailable:
            translation, provider = text, "original"
        if self._utt_id is None or not hyp.partial:
            if not hyp.partial:
                self._utt_id = uuid.uuid4().hex[:12]
            elif self._utt_id is None:
                self._utt_id = uuid.uuid4().hex[:12]
        utt_id = self._utt_id
        if not hyp.partial:
            self._utt_id = None
        ev = {
            "type": "partial" if hyp.partial else "final",
            "id": utt_id,
            "session_id": self.id,
            "text": text,
            "translation": translation,
            "src_lang": direction.src,
            "tgt_lang": direction.tgt,
            "speaker": direction.speaker,
            "provider": provider,
            "partial": hyp.partial,
            "final": not hyp.partial,
            "stt_ms": hyp.elapsed_ms,
            "mt_ms": mt_ms,
            "lang_prob": round(float(hyp.lang_prob or 0.0), 3),
            "src_name": display_name(direction.src),
            "tgt_name": display_name(direction.tgt),
        }
        self.emit(ev)
        if not hyp.partial and self.tts and translation:
            self.emit({"type": "speak_browser", "id": utt_id, "lang": direction.tgt, "text": translation})

    def wait_idle(self, timeout: float = 8.0) -> None:
        self._pool.submit(lambda: None).result(timeout=timeout)

    def close(self) -> None:
        self.closed = True
        self.started = False
        self._pool.shutdown(wait=False, cancel_futures=True)
