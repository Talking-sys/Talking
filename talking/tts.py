"""TTS chain. Default product path is the browser Web Speech API.

Server gTTS (MIT) is optional via APP_TTS_PROVIDERS=gtts.
edge-tts / piper / pyttsx3 are GPL/LGPL extras and stay disabled unless
installed from requirements-optional.txt AND listed in APP_TTS_PROVIDERS.
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .languages import DEFAULT_EDGE_VOICES, edge_locale_prefix, gtts_code

log = logging.getLogger(__name__)


@dataclass
class TTSResult:
    audio: bytes
    mime: str
    provider: str
    elapsed_ms: int


class GTTS:
    name = "gtts"

    def __init__(self, s: Settings):
        self.s = s

    def synth(self, text, lang):
        from gtts import gTTS
        from gtts.lang import tts_langs
        code = gtts_code(lang)
        if code not in tts_langs():
            raise LookupError(f"gTTS: language '{lang}' is not supported")
        buf = io.BytesIO()
        gTTS(text=text, lang=code).write_to_fp(buf)
        return buf.getvalue(), "audio/mpeg"


class EdgeTTS:
    """Optional LGPL extra — not imported unless selected."""
    name = "edge"
    _voices: list[dict] | None = None
    _voices_lock = threading.Lock()

    def __init__(self, s: Settings):
        self.s = s

    def voice_for(self, lang: str) -> str | None:
        if lang in DEFAULT_EDGE_VOICES:
            return DEFAULT_EDGE_VOICES[lang]
        with self._voices_lock:
            if EdgeTTS._voices is None:
                import edge_tts
                EdgeTTS._voices = asyncio.run(edge_tts.list_voices())
        prefix = edge_locale_prefix(lang).lower()
        cands = [v for v in EdgeTTS._voices
                 if v["Locale"].lower() == prefix or v["Locale"].lower().startswith(prefix + "-")]
        return cands[0]["ShortName"] if cands else None

    def synth(self, text: str, lang: str) -> tuple[bytes, str]:
        import edge_tts
        voice = self.voice_for(lang)
        if not voice:
            raise LookupError(f"edge-tts: no voice for '{lang}'")

        async def run() -> bytes:
            buf = bytearray()
            async for chunk in edge_tts.Communicate(text, voice, rate=self.s.tts_rate).stream():
                if chunk["type"] == "audio":
                    buf += chunk["data"]
            return bytes(buf)

        data = asyncio.run(run())
        if not data:
            raise RuntimeError("edge-tts returned empty audio")
        return data, "audio/mpeg"


class PiperTTS:
    name = "piper"

    def __init__(self, s: Settings):
        self.dir = Path(s.piper_voices_dir)

    def synth(self, text, lang):
        raise LookupError("piper-tts is an optional GPL extra and is not enabled in the default build")


class Pyttsx3TTS:
    name = "pyttsx3"

    def __init__(self, s: Settings):
        pass

    def synth(self, text, lang):
        raise LookupError("pyttsx3/espeak-ng is an optional GPL extra and is not enabled in the default build")


OPTIONAL_COPYLEFT = {"edge": EdgeTTS, "piper": PiperTTS, "pyttsx3": Pyttsx3TTS}
ENGINES = {"gtts": GTTS, **OPTIONAL_COPYLEFT}


class TTSEngine:
    def __init__(self, settings: Settings, providers: list[str] | None = None):
        self.s = settings
        names = providers if providers is not None else [
            p.strip() for p in settings.tts_providers.split(",") if p.strip()
        ]
        self.engines = []
        self.last_errors: list[str] = []
        for n in names:
            if n not in ENGINES:
                log.warning("Unknown TTS provider: %s", n)
                continue
            if n in OPTIONAL_COPYLEFT:
                log.warning("TTS provider '%s' is a copyleft extra and is disabled by default", n)
                continue
            self.engines.append(ENGINES[n](settings))

    def synth(self, text: str, lang: str) -> TTSResult | None:
        self.last_errors = []
        if not text.strip() or not self.s.tts_enabled or not self.engines:
            return None
        for e in self.engines:
            t0 = time.time()
            try:
                data, mime = e.synth(text, lang)
                return TTSResult(data, mime, e.name, int((time.time() - t0) * 1000))
            except Exception as ex:
                self.last_errors.append(f"{e.name}: {type(ex).__name__}: {str(ex)[:120]}")
                log.info("TTS %s failed: %s", e.name, ex)
        return None
