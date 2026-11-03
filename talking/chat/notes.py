"""Voice/video note helpers: optional STT + translation on the audio track.

WAV is decoded with the stdlib. WebM/MP4/OGG need ffmpeg on PATH; if it is
missing the note is still stored and ``transcript_status`` is ``skipped``.
Whisper is not loaded on upload (large-v3 is heavy) — we only transcribe when
the engine already has a ready STT (tests inject FakeSTT).
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
    """Fill transcript/translation in-place. Never raises to the caller."""
    if not getattr(engine.s, "chat_transcribe", True):
        message.transcript_status = "skipped"
        message.transcript_reason = "CHAT_TRANSCRIBE disabled"
        return message
    stt = getattr(engine, "stt", None)
    if stt is None or not getattr(stt, "ready", False):
        message.transcript_status = "skipped"
        message.transcript_reason = "STT is not loaded; transcript not attached"
        return message
    audio = decode_pcm16k(data, media.container, getattr(engine.s, "sample_rate", 16000))
    if audio is None or len(audio) < 160:
        message.transcript_status = "skipped"
        message.transcript_reason = (
            f"no PCM decoder for {media.container} "
            "(WAV works without ffmpeg; WebM/MP4/OGG need ffmpeg on PATH)"
        )
        return message
    try:
        result = stt.transcribe(audio)
    except Exception as e:  # noqa: BLE001 — note must still send
        log.warning("note STT failed: %s", e)
        message.transcript_status = "error"
        message.transcript_reason = str(e)[:200]
        return message
    message.transcript = result.text
    message.src_lang = result.lang
    message.transcript_status = "ok"
    tgt = (target_lang or engine.store.get("target_lang") or "en").strip() or "en"
    message.tgt_lang = tgt
    try:
        tr = engine.translator.translate(result.text, result.lang, tgt)
        message.translation = tr.text
        message.translation_provider = tr.provider
    except Exception as e:  # noqa: BLE001
        message.translation = result.text
        message.translation_provider = "original"
        message.transcript_reason = f"translation failed, showing original: {e}"[:200]
    return message
