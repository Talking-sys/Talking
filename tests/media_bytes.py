"""Tiny in-memory media fixtures for chat-note tests."""
from __future__ import annotations

import io
import math
import struct
import wave


def make_wav_bytes(duration_s: float = 0.4, sr: int = 16000, freq: float = 440.0) -> bytes:
    n = int(sr * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / sr)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def webm_header(n: int = 64) -> bytes:
    return (b"\x1a\x45\xdf\xa3" + b"B\x86\x81\x01").ljust(n, b"\x00")


def mp4_header(n: int = 64) -> bytes:
    return (b"\x00\x00\x00\x18ftypisom").ljust(n, b"\x00")
