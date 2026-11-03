"""Server-side energy VAD fallback (client Silero/energy VAD is preferred)."""
from __future__ import annotations

import numpy as np


class VADSegmenter:
    def __init__(self, sample_rate: int = 16000, frame_ms: int = 30, silence_ms: int = 800,
                 min_segment_s: float = 0.35, max_segment_s: float = 18.0,
                 threshold: float = 0.0, preroll_ms: int = 300):
        self.sr = sample_rate
        self.frame = int(sample_rate * frame_ms / 1000)
        self.silence_frames = max(1, silence_ms // frame_ms)
        self.min_samples = int(min_segment_s * sample_rate)
        self.max_samples = int(max_segment_s * sample_rate)
        self.fixed_threshold = threshold
        self.noise_floor = 0.003
        self.preroll_frames = max(1, preroll_ms // frame_ms)
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll: list[np.ndarray] = []
        self._speech: list[np.ndarray] = []
        self._speech_len = 0
        self._silence_run = 0
        self.speaking = False

    @property
    def threshold(self) -> float:
        if self.fixed_threshold > 0:
            return self.fixed_threshold
        return max(0.008, self.noise_floor * 3.0)

    def set_silence_ms(self, silence_ms: int, frame_ms: int = 30) -> None:
        self.silence_frames = max(1, int(silence_ms) // frame_ms)

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll.clear()
        self._speech.clear()
        self._speech_len = 0
        self._silence_run = 0
        self.speaking = False

    def feed(self, samples: np.ndarray) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        buf = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        n = len(buf) // self.frame
        for i in range(n):
            fr = buf[i * self.frame:(i + 1) * self.frame]
            rms = float(np.sqrt(np.mean(fr * fr)) + 1e-12)
            voiced = rms > self.threshold
            if not self.speaking:
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * min(rms, 0.05)
                self._preroll.append(fr)
                if len(self._preroll) > self.preroll_frames:
                    self._preroll.pop(0)
                if voiced:
                    self.speaking = True
                    self._speech = list(self._preroll)
                    self._speech_len = sum(len(x) for x in self._speech)
                    self._preroll.clear()
                    self._silence_run = 0
            else:
                self._speech.append(fr)
                self._speech_len += len(fr)
                self._silence_run = 0 if voiced else self._silence_run + 1
                if self._silence_run >= self.silence_frames or self._speech_len >= self.max_samples:
                    seg = self._finish()
                    if seg is not None:
                        out.append(seg)
        self._pending = buf[n * self.frame:]
        return out

    def flush(self) -> np.ndarray | None:
        if self._pending.size and self.speaking:
            self._speech.append(self._pending)
            self._speech_len += len(self._pending)
        self._pending = np.zeros(0, dtype=np.float32)
        return self._finish() if self.speaking else None

    def _finish(self) -> np.ndarray | None:
        seg = np.concatenate(self._speech) if self._speech else None
        self._speech, self._speech_len, self._silence_run = [], 0, 0
        self.speaking = False
        if seg is None or len(seg) < self.min_samples:
            return None
        return seg


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(samples: np.ndarray) -> bytes:
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype("<i2")
    return pcm.tobytes()
