"""Offline JSONL ring buffer. Entries are deleted only after a successful translation."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path


class OfflineQueue:
    def __init__(self, data_dir: str | Path, max_audio_seconds: float = 180.0):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "offline_queue.jsonl"
        self.meta_path = self.dir / "offline_meta.json"
        self.max_audio_seconds = max_audio_seconds
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self.dropped_segments = 0
        self.overflow = False
        self._seq = 0
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if self.meta_path.exists():
            try:
                m = json.loads(self.meta_path.read_text(encoding="utf-8"))
                self.dropped_segments = int(m.get("dropped_segments", 0))
                self.overflow = bool(m.get("overflow", False))
            except Exception:
                pass
        self._seq = max([it.get("seq", 0) for it in self._items], default=0)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _persist(self) -> None:
        self._atomic_write(self.path, "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in self._items))
        self._atomic_write(self.meta_path, json.dumps(
            {"dropped_segments": self.dropped_segments, "overflow": self.overflow}))

    def _evict(self, capacity: float | None = None) -> list[dict]:
        evicted = []
        cap = max(self.max_audio_seconds, capacity or 0)
        while self.audio_seconds() > cap and self._items:
            it = self._items.pop(0)
            evicted.append(it)
            if it["kind"] == "segment":
                self.dropped_segments += 1
                self.overflow = True
        return evicted

    def add_segment(self, text: str, src_lang: str, tgt_lang: str, start: float, end: float,
                    history_id: int | None = None, seg_id: str | None = None, lang_prob: float = 0.0,
                    capacity: float | None = None) -> tuple[dict, list[dict]]:
        with self._lock:
            self._seq += 1
            item = {"kind": "segment", "seq": self._seq, "seg_id": seg_id or uuid.uuid4().hex[:12],
                    "history_id": history_id, "start": start, "end": end,
                    "duration": round(end - start, 3), "src_lang": src_lang, "tgt_lang": tgt_lang,
                    "lang_prob": lang_prob, "text": text, "queued_at": time.time()}
            self._items.append(item)
            evicted = self._evict(capacity)
            self._persist()
            return item, evicted

    def add_command(self, tgt_lang: str, ts: float | None = None, text: str = "") -> dict:
        with self._lock:
            self._seq += 1
            item = {"kind": "command", "seq": self._seq, "seg_id": uuid.uuid4().hex[:12],
                    "ts": ts or time.time(), "start": ts or time.time(), "duration": 0.0,
                    "tgt_lang": tgt_lang, "text": text}
            self._items.append(item)
            self._persist()
            return item

    def persist_extra(self, seg_id: str, **fields) -> None:
        with self._lock:
            for i in self._items:
                if i["seg_id"] == seg_id:
                    i.update(fields)
            self._persist()

    def remove_where(self, pred) -> int:
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if not pred(i)]
            self._persist()
            return before - len(self._items)

    def items(self) -> list[dict]:
        with self._lock:
            return sorted(self._items, key=lambda i: (i["start"], i["seq"]))

    def remove(self, seg_id: str) -> None:
        with self._lock:
            self._items = [i for i in self._items if i["seg_id"] != seg_id]
            if not self._items:
                self.overflow = False
            self._persist()

    def audio_seconds(self) -> float:
        return sum(i.get("duration", 0.0) for i in self._items if i["kind"] == "segment")

    def pending_segments(self) -> int:
        return sum(1 for i in self._items if i["kind"] == "segment")

    def __len__(self) -> int:
        return len(self._items)

    def ack_overflow(self) -> None:
        with self._lock:
            self.overflow = False
            self._persist()

    def status(self) -> dict:
        with self._lock:
            return {"pending": self.pending_segments(), "commands": len(self._items) - self.pending_segments(),
                    "audio_seconds": round(self.audio_seconds(), 1), "capacity_seconds": self.max_audio_seconds,
                    "dropped_segments": self.dropped_segments, "overflow": self.overflow}

    def replay(self, translate_fn, on_command=None, on_done=None) -> tuple[int, str | None]:
        done = 0
        for it in self.items():
            if it["kind"] == "command":
                if on_command:
                    on_command(it)
                self.remove(it["seg_id"])
                continue
            try:
                res = translate_fn(it)
            except Exception as e:
                return done, f"{type(e).__name__}: {e}"
            if on_done:
                on_done(it, res)
            self.remove(it["seg_id"])
            done += 1
        return done, None
