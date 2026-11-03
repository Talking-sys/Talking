"""Speech pipeline: STT → local command? → translate (or offline queue) → events.

Independent of transport (WebSocket / tests). No licensing, ads, or admin.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from .archive import ArchiveManager, s3_from_env
from .buffer import RollingBuffer
from .commands import parse_command
from .commercial.client import CommercialClient
from .config import Settings, WHISPER_MODELS
from .ids import check_id, secure_dir
from .languages import WHISPER_LANGUAGES, display_name
from .offline_queue import OfflineQueue
from .settings_store import SettingsStore, clamp_model, clamp_pause
from .stt import FakeSTTEngine, STTEngine, STTUnavailable
from .translate import TranslationUnavailable, Translator
from .tts import TTSEngine
from .vad import VADSegmenter

log = logging.getLogger(__name__)
Emit = Callable[[dict], None]


class Session:
    def __init__(self, engine: "Engine", emit: Emit, user_id: str | None = None):
        st = engine.store
        self.engine = engine
        self.emit = emit
        self.id = time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:6]
        raw_uid = user_id or st.get("user_id") or "local"
        try:
            self.user_id = check_id(raw_uid)
        except ValueError:
            self.user_id = "local"
        self.target = st.get("target_lang", "en")
        self.partner = st.get("partner_lang", "ru")
        self.tts_mode = st.get("tts_mode", "browser")
        self.pause_ms = clamp_pause(st.get("pause_ms"))
        self.whisper_model = clamp_model(st.get("whisper_model") or engine.s.whisper_model)
        self.client_vad = True
        self.started = False
        self.vad = self._make_vad()
        self._utt: list[np.ndarray] = []
        self._utt_len = 0
        self._utt_start: float | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"sess-{self.id}")
        self.closed = False

    def _make_vad(self) -> VADSegmenter:
        s = self.engine.s
        return VADSegmenter(s.sample_rate, s.block_ms, self.pause_ms, s.min_segment_s, s.max_segment_s, s.vad_threshold)

    def configure(self, **kw) -> dict:
        if kw.get("target_lang") in WHISPER_LANGUAGES:
            self.target = kw["target_lang"]
        if kw.get("partner_lang") in WHISPER_LANGUAGES:
            self.partner = kw["partner_lang"]
        if kw.get("tts_mode") in ("server", "browser", "off"):
            self.tts_mode = kw["tts_mode"]
        if kw.get("pause_ms") is not None:
            self.pause_ms = clamp_pause(kw["pause_ms"])
            self.vad.set_silence_ms(self.pause_ms, self.engine.s.block_ms)
        if "client_vad" in kw:
            self.client_vad = bool(kw["client_vad"])
        if kw.get("whisper_model") in WHISPER_MODELS:
            self.whisper_model = kw["whisper_model"]
            self.engine.s.whisper_model = self.whisper_model
            if hasattr(self.engine.stt, "load"):
                try:
                    self.engine.stt.load(self.whisper_model)
                except STTUnavailable as e:
                    self.emit({"type": "notice", "level": "warning", "text": str(e)})
        self.engine.store.update(target_lang=self.target, partner_lang=self.partner,
                                 tts_mode=self.tts_mode, pause_ms=self.pause_ms,
                                 whisper_model=self.whisper_model)
        return self.state()

    def state(self) -> dict:
        return {"type": "config", "session_id": self.id, "target_lang": self.target,
                "partner_lang": self.partner, "tts_mode": self.tts_mode, "pause_ms": self.pause_ms,
                "client_vad": self.client_vad, "whisper_model": self.whisper_model,
                "recent_targets": self.engine.store.get("recent_targets")}

    def feed_pcm(self, samples: np.ndarray) -> None:
        now = time.time()
        if self.client_vad:
            if self._utt_start is None:
                self._utt_start = now - len(samples) / self.engine.s.sample_rate
            self._utt.append(samples)
            self._utt_len += len(samples)
            if self._utt_len >= self.engine.s.max_segment_s * self.engine.s.sample_rate:
                self.end_of_utterance(forced=True)
            return
        was = self.vad.speaking
        for seg in self.vad.feed(samples):
            self.submit(seg, now - len(seg) / self.engine.s.sample_rate)
        if self.vad.speaking != was:
            self.emit({"type": "vad", "speaking": self.vad.speaking})

    def end_of_utterance(self, forced: bool = False) -> None:
        if self.client_vad:
            if self._utt:
                seg = np.concatenate(self._utt)
                start = self._utt_start or time.time() - len(seg) / self.engine.s.sample_rate
                self._utt, self._utt_len, self._utt_start = [], 0, None
                if len(seg) >= self.engine.s.min_segment_s * self.engine.s.sample_rate:
                    self.submit(seg, start)
        else:
            seg = self.vad.flush()
            if seg is not None:
                self.submit(seg, time.time() - len(seg) / self.engine.s.sample_rate)
        if forced:
            self.emit({"type": "notice", "level": "info",
                       "text": "Utterance exceeded the 15–20 s cap and was split."})

    def submit(self, audio: np.ndarray, start: float):
        if self.closed:
            return None
        return self._pool.submit(self._safe_process, audio, start)

    def _safe_process(self, audio, start):
        try:
            return self.engine.process_segment(self, audio, start)
        except Exception as e:
            log.exception("segment processing failed")
            self.emit({"type": "notice", "level": "error", "text": f"Processing error: {e}"})

    def wait_idle(self, timeout: float = 30) -> None:
        self._pool.submit(lambda: None).result(timeout=timeout)

    def close(self):
        self.closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.engine.unregister(self)


class Engine:
    def __init__(self, settings: Settings, translator: Translator | None = None,
                 stt: STTEngine | FakeSTTEngine | None = None, tts: TTSEngine | None = None,
                 archive: ArchiveManager | None = None, start_background: bool = True):
        self.s = settings
        d = secure_dir(settings.data_dir)
        self.started_at = time.time()
        self.store = SettingsStore(d / "settings.json", {
            "pause_ms": settings.silence_ms,
            "whisper_model": settings.normalized_whisper_model(),
        })
        self.stt = stt or STTEngine(settings)
        self.translator = translator or Translator(settings)
        self.tts = tts or TTSEngine(settings)
        self._histories: dict[str, RollingBuffer] = {}
        self.offline = OfflineQueue(d, settings.buffer_seconds)
        self.archive = archive or ArchiveManager(d, remote=s3_from_env())
        self.commercial = CommercialClient.from_settings(settings)
        self.net = "online" if not self.offline.pending_segments() else "offline"
        self._sessions: set[Session] = set()
        self._slock = threading.Lock()
        self._stop = threading.Event()
        if start_background:
            threading.Thread(target=self._probe_loop, name="net-probe", daemon=True).start()

    def history_for(self, user_id: str) -> RollingBuffer:
        try:
            user_id = check_id(user_id)
        except ValueError:
            user_id = "local"
        h = self._histories.get(user_id)
        if h is None:
            h = RollingBuffer(secure_dir(Path(self.s.data_dir) / "history" / user_id), self.s.buffer_seconds,
                              sample_rate=self.s.sample_rate)
            self._histories[user_id] = h
        return h

    def new_session(self, emit: Emit, user_id: str | None = None) -> Session:
        s = Session(self, emit, user_id)
        with self._slock:
            self._sessions.add(s)
        return s

    def unregister(self, s: Session):
        with self._slock:
            self._sessions.discard(s)

    def broadcast(self, ev: dict):
        with self._slock:
            sessions = list(self._sessions)
        for s in sessions:
            try:
                s.emit(ev)
            except Exception:
                pass

    def status(self) -> dict:
        q = self.offline.status()
        labels = {"online": "Online", "offline": "Offline — speech is queued",
                  "syncing": f"Syncing {q['pending']} segments"}
        ev = {"type": "status", "net": self.net, "label": labels.get(self.net, self.net), **q,
              "forced_offline": self.translator.force_offline,
              "stt_ready": self.stt.ready, "stt_error": getattr(self.stt, "error", None),
              "stt_model": getattr(self.stt, "loaded_name", None) or self.s.whisper_model,
              "providers": [p.name for p in self.translator.active_providers()],
              "translate_notices": self.translator.notices,
              "archive": self.archive.status(), "app_name": self.s.app_name,
              "sessions": len(self._sessions),
              "tts_default": "browser",
              "commercial": self.commercial.status()}
        if q["overflow"]:
            ev["warning"] = (f"Offline buffer overflow, oldest segments overwritten "
                             f"(dropped: {q['dropped_segments']})")
        return ev

    def set_force_offline(self, value: bool) -> None:
        self.translator.force_offline = bool(value)
        self.broadcast(self.status())

    def _set_net(self, state: str):
        if state != self.net:
            self.net = state
            self.broadcast(self.status())

    def process_segment(self, sess: Session, audio: np.ndarray, start: float) -> dict | None:
        dur = len(audio) / self.s.sample_rate
        end = start + dur
        sess.emit({"type": "processing", "duration": round(dur, 2)})
        try:
            r = self.stt.transcribe(audio)
        except STTUnavailable as e:
            sess.emit({"type": "notice", "level": "error", "text": str(e)})
            return None
        if not r.text:
            sess.emit({"type": "empty"})
            return None
        sess.emit({"type": "detected", "lang": r.lang, "prob": round(r.lang_prob, 3), "name": display_name(r.lang)})

        cmd = parse_command(r.text)
        if cmd:
            sess.target = cmd
            self.store.update(target_lang=cmd)
            if self.net != "online":
                self.offline.add_command(cmd, ts=start, text=r.text)
            sess.emit({"type": "command", "target_lang": cmd, "name": display_name(cmd), "text": r.text,
                       "toast": f"Target language: {display_name(cmd)}"})
            sess.emit(sess.state())
            return {"command": cmd}

        # Bidirectional: if the speaker used the target language, translate back to partner.
        if r.lang == sess.target:
            tgt = sess.partner if sess.partner != sess.target else ("ru" if sess.target != "ru" else "en")
        else:
            tgt = sess.target
            sess.partner = r.lang
            self.store.update(partner_lang=r.lang)

        hist = self.history_for(sess.user_id)
        entry = hist.add(r.lang, tgt, r.text, dur, audio=audio, wall_time=start)
        hist.update_timings(entry.id, stt=r.elapsed_ms)
        seg_id = uuid.uuid4().hex[:12]
        base = {"id": entry.id, "segment_id": seg_id, "src_lang": r.lang, "tgt_lang": tgt,
                "prob": round(r.lang_prob, 3), "text": r.text, "start": start, "end": end,
                "clock": entry.clock(), "timecode": entry.timecode(), "stt_ms": r.elapsed_ms,
                "session_id": sess.id, "user_id": sess.user_id}
        sess.emit({"type": "source", **{k: v for k, v in base.items() if k != "user_id"}})

        if self.net == "online" or not self.offline.pending_segments():
            try:
                tr = self.translator.translate(r.text, r.lang, tgt)
            except TranslationUnavailable as e:
                log.warning("translation unavailable: %s", e)
                tr = None
                self._enqueue_offline(sess, base, entry.id, str(e))
            if tr is not None:
                if self.net != "online":
                    self._set_net("online")
                self._deliver(sess, base, tr.text, tr.provider, tr.elapsed_ms, deferred=False)
                if self.offline.pending_segments():
                    threading.Thread(target=self.sync, daemon=True).start()
                return {"text": r.text, "translation": tr.text, "lang": r.lang, "tgt": tgt}
            return {"text": r.text, "queued": True, "lang": r.lang, "tgt": tgt}
        self._enqueue_offline(sess, base, entry.id, "offline")
        return {"text": r.text, "queued": True, "lang": r.lang, "tgt": tgt}

    def _enqueue_offline(self, sess: Session, base: dict, history_id: int, why: str):
        item, evicted = self.offline.add_segment(base["text"], base["src_lang"], base["tgt_lang"], base["start"],
                                                 base["end"], history_id=history_id, seg_id=base["segment_id"],
                                                 lang_prob=base["prob"])
        self.offline.persist_extra(item["seg_id"], session_id=sess.id, user_id=sess.user_id)
        self._set_net("offline")
        sess.emit({"type": "queued", "id": history_id, "segment_id": base["segment_id"],
                   "text": "Translator unreachable — phrase saved and will be sent in order later.",
                   "why": why})
        if evicted:
            self.broadcast({"type": "notice", "level": "warning",
                            "text": "Offline buffer overflow, oldest segments overwritten."})
        self.broadcast(self.status())

    def _deliver(self, sess: Session | None, base: dict, text: str, provider: str, mt_ms: int,
                 deferred: bool):
        uid = base.get("user_id") or (sess.user_id if sess else "local")
        if base.get("id"):
            self.history_for(uid).update_translation(base["id"], text, provider, mt=mt_ms)
        ev = {"type": "translation", **{k: v for k, v in base.items() if k != "user_id"},
              "translation": text, "provider": provider, "mt_ms": mt_ms, "deferred": deferred}
        emit = sess.emit if sess else (lambda e: None)
        emit(ev)
        self.archive.archive({
            "segment_id": base["segment_id"],
            "session_id": base.get("session_id") or (sess.id if sess else "unknown"),
            "user_id": uid,
            "start": base["start"], "end": base["end"],
            "src_lang": base["src_lang"], "tgt_lang": base["tgt_lang"],
            "lang_prob": base.get("prob"), "text": base["text"],
            "translation": text, "provider": provider, "deferred": deferred,
            "archived_at": time.time(),
        })
        if sess and sess.tts_mode == "browser":
            sess.emit({"type": "speak_browser", "id": base.get("id"), "lang": base["tgt_lang"], "text": text})
        elif sess and sess.tts_mode == "server":
            res = self.tts.synth(text, base["tgt_lang"])
            if res is None:
                sess.emit({"type": "speak_browser", "id": base.get("id"), "lang": base["tgt_lang"], "text": text,
                           "reason": "; ".join(self.tts.last_errors) or "server TTS unavailable"})

    def sync(self) -> dict:
        if not self._stop.is_set():
            self._set_net("syncing")

        def translate_fn(it):
            tr = self.translator.translate(it["text"], it["src_lang"], it["tgt_lang"])
            base = {"id": it.get("history_id"), "segment_id": it["seg_id"], "src_lang": it["src_lang"],
                    "tgt_lang": it["tgt_lang"], "text": it["text"], "start": it["start"], "end": it["end"],
                    "prob": it.get("lang_prob", 0), "session_id": it.get("session_id"),
                    "user_id": it.get("user_id"), "clock": "", "timecode": ""}
            with self._slock:
                sess = next((s for s in self._sessions if s.user_id == it.get("user_id")), None)
            self._deliver(sess, base, tr.text, tr.provider, tr.elapsed_ms, deferred=True)
            return tr

        def on_command(it):
            self.store.update(target_lang=it["tgt_lang"])
            self.broadcast({"type": "command", "target_lang": it["tgt_lang"],
                            "name": display_name(it["tgt_lang"]), "deferred": True})

        n, err = self.offline.replay(translate_fn, on_command=on_command)
        self._set_net("offline" if err else "online")
        return {"synced": n, "error": err}

    def _probe_loop(self):
        while not self._stop.wait(self.s.probe_interval_s):
            try:
                if self.offline.pending_segments() and self.translator.probe():
                    self.sync()
            except Exception:
                log.exception("probe")

    def close(self):
        self._stop.set()
        for h in self._histories.values():
            h.close()
