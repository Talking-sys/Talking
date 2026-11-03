"""Voice/video note helpers: PCM decode for on-demand transcription.

WAV is decoded with the stdlib. WebM/MP4/OGG need ffmpeg on PATH. Upload does
**not** transcribe by default (see ``talking/chat/transcribe.py``); premium
users tap «Расшифровать» after the note is stored.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess  # nosec B404
import tempfile
import wave
from pathlib import Path

import numpy as np

from .media import StoredMedia
from .models import Message

log = logging.getLogger(__name__)

_FFMPEG_FMT = {"wav": "wav", "webm": "webm", "ogg": "ogg", "mp4": "mp4", "mp3": "mp3"}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _wav_to_mono_f32(data: bytes, sample_rate: int) -> np.ndarray | None:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            nch = w.getnchannels()
            sw = w.getnframes()
            sr = w.getframerate()
            width = w.getsampwidth()
            raw = w.readframes(sw)
    except (wave.Error, EOFError, OSError):
        return None
    if width == 2:
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        pcm = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        return None
    if nch > 1:
        pcm = pcm.reshape(-1, nch).mean(axis=1)
    if sr != sample_rate and len(pcm) > 1:
        n = max(1, int(round(len(pcm) * sample_rate / sr)))
        xp = np.linspace(0.0, 1.0, len(pcm), endpoint=False)
        xq = np.linspace(0.0, 1.0, n, endpoint=False)
        pcm = np.interp(xq, xp, pcm).astype(np.float32)
    return np.ascontiguousarray(pcm, dtype=np.float32)


def decode_pcm16k(data: bytes, container: str, sample_rate: int = 16000) -> np.ndarray | None:
    if container == "wav":
        return _wav_to_mono_f32(data, sample_rate)
    fmt = _FFMPEG_FMT.get(container)
    if not fmt:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"in.{fmt}"
        dst = Path(td) / "out.wav"
        src.write_bytes(data)
        cmd = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "wav", str(dst),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=25, capture_output=True)  # nosec B603
        except (subprocess.SubprocessError, OSError) as e:
            log.info("ffmpeg could not decode %s: %s", container, e)
            return None
        if not dst.is_file():
            return None
        return _wav_to_mono_f32(dst.read_bytes(), sample_rate)


def attach_transcript(engine, message: Message, media: StoredMedia, data: bytes,
                      target_lang: str | None) -> Message:
    """Legacy PR #3 helper. Prefer ``transcribe.run_pipeline`` (on-demand).

    Kept so ``CHAT_TRANSCRIBE_ON_UPLOAD=1`` can still fill a note in-process.
    Status values are ``done`` / ``failed`` (``ok`` / ``skipped`` aliases remain
    readable from older JSONL).
    """
    from .transcribe import run_pipeline

    if not getattr(engine.s, "chat_transcribe", True):
        message.transcript_status = "failed"
        message.transcript_reason = "CHAT_TRANSCRIBE disabled"
        return message
    return run_pipeline(engine, message, media, data, target_lang)
