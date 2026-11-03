"""On-demand voice/video-note transcription (premium-gated).

Default provider is local faster-whisper: GPL-compatible, private, audio never
leaves the server. A cloud OpenAI-compatible provider exists for later wiring
and stays **off** unless ``CHAT_STT_PROVIDER=openai-compatible`` plus URL/key.

Jobs are in-process (ThreadPoolExecutor). Status is ``pending`` / ``done`` /
``failed``. Repeat taps are idempotent: a completed transcript is cached on the
message; an in-flight job is not restarted.
"""
from __future__ import annotations

import io
import logging
import shutil
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from talking.stt import STTUnavailable

from .media import MediaError, MediaLibrary, StoredMedia
from .models import NOTE_TYPES, Message, transcript_view
from .notes import decode_pcm16k, ffmpeg_available
from .store import ChatStore

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Legacy PR #3 values still accepted when reading stored messages.
_DONE_ALIASES = frozenset({STATUS_DONE, "ok"})
_FAILED_ALIASES = frozenset({STATUS_FAILED, "error", "skipped"})

DEFAULT_PROVIDER = "faster-whisper"
CLOUD_PROVIDER = "openai-compatible"


class TranscriptionUnavailable(RuntimeError):
    """Provider cannot run (disabled, missing binary, or misconfigured)."""


@dataclass
class TranscriptResult:
    text: str
    language: str
    language_probability: float
    duration: float
    provider: str
    model: str


class TranscriptionProvider(Protocol):
    """Pluggable STT. Implementations must not live in the proprietary repo."""

    name: str

    def available(self) -> bool:
        ...

    def transcribe(self, pcm: np.ndarray, language: str | None = None) -> TranscriptResult:
        ...


class FasterWhisperProvider:
    """Local Whisper via the engine's STT (faster-whisper).

    Chosen as the default because:

    * it is already the project STT (``talking/stt.py``);
    * faster-whisper / CTranslate2 are MIT — fine next to this GPL core;
    * audio is decoded and recognized on the Talking server, never uploaded
      to a third-party ASR (matches the owner's privacy requirement);
    * language is auto-detected.

    Cloud ASR can be swapped in through ``TranscriptionProvider`` without
    changing the job runner or HTTP surface.
    """

    name = DEFAULT_PROVIDER

    def __init__(self, stt: Any, model_hint: str = "faster-whisper"):
        self.stt = stt
        self.model_hint = model_hint

    def available(self) -> bool:
        return self.stt is not None

    def transcribe(self, pcm: np.ndarray, language: str | None = None) -> TranscriptResult:
        stt = self.stt
        if stt is None:
            raise TranscriptionUnavailable("STT engine is not configured")
        if hasattr(stt, "load"):
            try:
                stt.load()
            except STTUnavailable as e:
                raise TranscriptionUnavailable(str(e)) from e
            except Exception as e:  # noqa: BLE001 — surface as job failure
                raise TranscriptionUnavailable(str(e)[:200]) from e
        try:
            result = stt.transcribe(pcm, language=language)
        except STTUnavailable as e:
            raise TranscriptionUnavailable(str(e)) from e
        model = getattr(stt, "loaded_name", None) or self.model_hint
        return TranscriptResult(
            text=(result.text or "").strip(),
            language=result.lang,
            language_probability=float(getattr(result, "lang_prob", 0.0) or 0.0),
            duration=float(getattr(result, "duration", 0.0) or 0.0),
            provider=self.name,
            model=str(model),
        )


class OpenAICompatibleProvider:
    """Cloud STT (OpenAI / xAI-style ``/v1/audio/transcriptions``).

    **Off by default.** Enabling it sends PCM/WAV to ``CHAT_STT_API_URL``, so
    audio leaves the server. Kept in the GPL core as a thin HTTP client only —
    no proprietary code.
    """

    name = CLOUD_PROVIDER

    def __init__(self, api_url: str = "", api_key: str = "", model: str = "whisper-1",
                 timeout: float = 30.0):
        self.api_url = (api_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or "whisper-1"
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_url and self.api_key)

    def transcribe(self, pcm: np.ndarray, language: str | None = None) -> TranscriptResult:
        if not self.available():
            raise TranscriptionUnavailable(
                "cloud STT is disabled (set CHAT_STT_PROVIDER=openai-compatible "
                "and CHAT_STT_API_URL / CHAT_STT_API_KEY)"
            )
        import httpx

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            clipped = np.clip(pcm, -1.0, 1.0)
            w.writeframes((clipped * 32767.0).astype("<i2").tobytes())
        wav = buf.getvalue()
        url = self.api_url
        if not url.endswith("/audio/transcriptions"):
            url = url + "/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data: dict[str, str] = {"model": self.model}
        if language:
            data["language"] = language
        files = {"file": ("note.wav", wav, "audio/wav")}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(url, headers=headers, data=data, files=files)
                r.raise_for_status()
                body = r.json()
        except Exception as e:  # noqa: BLE001
            raise TranscriptionUnavailable(f"cloud STT failed: {type(e).__name__}") from e
        text = ""
        lang = language or "en"
        if isinstance(body, dict):
            text = str(body.get("text") or "").strip()
            lang = str(body.get("language") or lang)
        elif isinstance(body, str):
            text = body.strip()
        return TranscriptResult(
            text=text,
            language=lang,
            language_probability=0.0,
            duration=float(len(pcm) / 16000.0),
            provider=self.name,
            model=self.model,
        )


def build_provider(engine, settings=None) -> TranscriptionProvider:
    s = settings or getattr(engine, "s", None)
    name = (getattr(s, "chat_stt_provider", None) or DEFAULT_PROVIDER).strip().lower()
    if name in ("openai", "openai-compatible", "xai", CLOUD_PROVIDER):
        cloud = OpenAICompatibleProvider(
            api_url=getattr(s, "chat_stt_api_url", "") or "",
            api_key=getattr(s, "chat_stt_api_key", "") or "",
            model=getattr(s, "chat_stt_api_model", None) or "whisper-1",
        )
        if cloud.available():
            log.warning(
                "CHAT_STT_PROVIDER=%s sends note audio to %s — local faster-whisper is the private default",
                name, cloud.api_url,
            )
            return cloud
        log.warning("cloud STT requested but URL/key missing; falling back to faster-whisper")
    hint = getattr(getattr(engine, "stt", None), "loaded_name", None) or getattr(
        s, "whisper_model", DEFAULT_PROVIDER
    )
    return FasterWhisperProvider(getattr(engine, "stt", None), model_hint=str(hint))


def normalize_status(value: str | None) -> str | None:
    if not value:
        return None
    if value in _DONE_ALIASES:
        return STATUS_DONE
    if value in _FAILED_ALIASES:
        return STATUS_FAILED
    if value == STATUS_PENDING:
        return STATUS_PENDING
    return value


def apply_result(message: Message, result: TranscriptResult, *,
                 translation: str | None, tgt_lang: str | None,
                 translation_provider: str | None,
                 reason: str | None = None) -> Message:
    message.transcript = result.text
    message.src_lang = result.language
    message.tgt_lang = tgt_lang
    message.translation = translation
    message.translation_provider = translation_provider
    message.transcript_status = STATUS_DONE
    message.transcript_reason = reason
    message.transcript_provider = result.provider
    message.transcript_model = result.model
    message.transcript_created_at = time.time()
    message.transcript_duration = result.duration
    return message


def apply_failure(message: Message, reason: str) -> Message:
    message.transcript_status = STATUS_FAILED
    message.transcript_reason = (reason or "transcription failed")[:300]
    return message


def _translate(engine, text: str, src_lang: str, target_lang: str | None) -> tuple[str, str, str, str | None]:
    tgt = (target_lang or (engine.store.get("target_lang") if getattr(engine, "store", None) else None) or "en")
    tgt = str(tgt).strip() or "en"
    translator = getattr(engine, "translator", None)
    if not text or translator is None:
        return text, tgt, "original", None
    try:
        tr = translator.translate(text, src_lang, tgt)
        return tr.text, tgt, tr.provider, None
    except Exception as e:  # noqa: BLE001
        return text, tgt, "original", f"translation failed, showing original: {e}"[:200]


def run_pipeline(engine, message: Message, media: StoredMedia, data: bytes,
                 target_lang: str | None, provider: TranscriptionProvider | None = None) -> Message:
    """Decode → STT → optional translation. Sets done/failed on ``message``."""
    if media.container != "wav" and not ffmpeg_available():
        return apply_failure(
            message,
            f"ffmpeg is not on PATH; cannot decode {media.container} "
            "(WAV works without ffmpeg; WebM/MP4/OGG need ffmpeg)",
        )
    audio = decode_pcm16k(data, media.container, getattr(getattr(engine, "s", None), "sample_rate", 16000))
    if audio is None or len(audio) < 160:
        if media.container != "wav" and not shutil.which("ffmpeg"):
            return apply_failure(message, "ffmpeg is not on PATH")
        return apply_failure(
            message,
            f"no PCM decoder for {media.container} "
            "(WAV works without ffmpeg; WebM/MP4/OGG need ffmpeg on PATH)",
        )
    prov = provider or build_provider(engine)
    if not prov.available():
        return apply_failure(message, "STT provider is not available")
    try:
        result = prov.transcribe(audio)
    except TranscriptionUnavailable as e:
        return apply_failure(message, str(e)[:300])
    except Exception as e:  # noqa: BLE001
        log.warning("note STT failed: %s", e)
        return apply_failure(message, str(e)[:300])
    translation, tgt, tprov, treason = _translate(engine, result.text, result.language, target_lang)
    return apply_result(
        message, result, translation=translation, tgt_lang=tgt,
        translation_provider=tprov, reason=treason,
    )


class TranscriptionService:
    """Idempotent in-process job runner. One live job per message id."""

    def __init__(self, *, engine, store: ChatStore, media: MediaLibrary, hub,
                 settings, public_message, max_workers: int = 2):
        self.engine = engine
        self.store = store
        self.media = media
        self.hub = hub
        self.s = settings
        self._public = public_message
        self._lock = threading.Lock()
        self._inflight: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="note-stt")

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)

    def provider(self) -> TranscriptionProvider:
        return build_provider(self.engine, self.s)

    def cached_done(self, msg: Message) -> bool:
        return normalize_status(msg.transcript_status) == STATUS_DONE and bool(msg.transcript)

    def _stale_pending(self, msg: Message) -> bool:
        if normalize_status(msg.transcript_status) != STATUS_PENDING:
            return False
        started = self._inflight.get(msg.id)
        ttl = getattr(self.s, "chat_transcribe_job_ttl", 120) or 120
        if started is None:
            return True
        return (time.time() - started) > ttl

    def request(self, user_id: str, msg: Message, *, target_lang: str | None) -> Message:
        """Start or return the existing job. Caller already enforced entitlement."""
        with self._lock:
            fresh = self.store.get_message(user_id, msg.dialog_id, msg.id) or msg
            if self.cached_done(fresh):
                return fresh
            if normalize_status(fresh.transcript_status) == STATUS_PENDING and not self._stale_pending(fresh):
                return fresh
            fresh.transcript_status = STATUS_PENDING
            fresh.transcript_reason = None
            self.store.update_message(user_id, fresh)
            self._inflight[fresh.id] = time.time()
            job_msg = fresh
        self._publish(user_id, job_msg)
        self._pool.submit(self._run, user_id, job_msg.id, job_msg.dialog_id, target_lang)
        return job_msg

    def _run(self, user_id: str, message_id: str, dialog_id: str, target_lang: str | None) -> None:
        try:
            msg = self.store.get_message(user_id, dialog_id, message_id)
            if msg is None:
                return
            if msg.type not in NOTE_TYPES or not msg.media_id:
                apply_failure(msg, "not a voice or video note")
                self.store.update_message(user_id, msg)
                self._publish(user_id, msg)
                return
            try:
                stored = self.media.get(user_id, msg.media_id)
                blob = self.media.read_bytes(stored)
            except MediaError as e:
                apply_failure(msg, str(e))
                self.store.update_message(user_id, msg)
                self._publish(user_id, msg)
                return
            run_pipeline(self.engine, msg, stored, blob, target_lang, self.provider())
            self.store.update_message(user_id, msg)
            self._publish(user_id, msg)
        except Exception as e:  # noqa: BLE001 — never kill the pool
            log.exception("transcription job crashed: %s", e)
            try:
                msg = self.store.get_message(user_id, dialog_id, message_id)
                if msg is not None:
                    apply_failure(msg, "internal transcription error")
                    self.store.update_message(user_id, msg)
                    self._publish(user_id, msg)
            except Exception:
                log.debug("could not persist failed transcription", exc_info=True)
        finally:
            with self._lock:
                self._inflight.pop(message_id, None)

    def _publish(self, user_id: str, msg: Message) -> None:
        payload = transcript_view(msg)
        targets = [user_id]
        try:
            if self.store.is_group(msg.dialog_id):
                g = self.store.groups.get(msg.dialog_id)
                if g is not None:
                    targets = g.member_ids()
            for uid in targets:
                public = self._public(uid, msg)
                self.hub.publish(uid, {"type": "chat.message", "message": public})
                self.hub.publish(uid, {
                    "type": "chat.transcript",
                    "message_id": msg.id,
                    "dialog_id": msg.dialog_id,
                    "status": payload["status"],
                    "transcript": payload,
                })
        except Exception:
            log.debug("transcript hub publish failed", exc_info=True)
