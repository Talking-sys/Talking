"""Rolling 3-minute buffer on disk: SQLite texts + WAV audio. Thread-safe."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wall_time REAL NOT NULL,
    t_rel REAL NOT NULL,
    duration REAL NOT NULL,
    src_lang TEXT, tgt_lang TEXT,
    src_text TEXT, tgt_text TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    timings TEXT DEFAULT '{}',
    audio_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_end ON entries(wall_time);
"""


@dataclass
class Entry:
    id: int
    wall_time: float
    t_rel: float
    duration: float
    src_lang: str
    tgt_lang: str
    src_text: str
    tgt_text: str = ""
    provider: str = ""
    timings_ms: dict = field(default_factory=dict)
    audio_path: str | None = None

    @property
    def end_time(self) -> float:
        return self.wall_time + self.duration

    def timecode(self) -> str:
        m, s = divmod(int(self.t_rel), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def clock(self) -> str:
        return datetime.fromtimestamp(self.wall_time).strftime("%H:%M:%S")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timecode"] = self.timecode()
        d["clock"] = self.clock()
        d["has_audio"] = bool(self.audio_path)
        d.pop("audio_path", None)
        return d


def write_wav(path: str | Path, audio: np.ndarray, sample_rate: int = 16000) -> None:
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("only 16-bit WAV is supported")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


class RollingBuffer:
    def __init__(self, data_dir: str | Path, max_seconds: float = 180.0,
                 clock: Callable[[], float] = time.time, sample_rate: int = 16000,
                 keep_audio: bool = True):
        self.max_seconds = max_seconds
        self.sample_rate = sample_rate
        self.keep_audio = keep_audio
        self._clock = clock
        self.dir = Path(data_dir)
        self.audio_dir = self.dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.dir / "buffer.sqlite"), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self.session_start = clock()
        self.trim()

    def add(self, src_lang: str, tgt_lang: str, src_text: str, duration: float,
            audio: np.ndarray | None = None, wall_time: float | None = None) -> Entry:
        with self._lock:
            wt = wall_time if wall_time is not None else self._clock() - duration
            t_rel = max(0.0, wt - self.session_start)
            cur = self._db.execute(
                "INSERT INTO entries(wall_time,t_rel,duration,src_lang,tgt_lang,src_text) VALUES(?,?,?,?,?,?)",
                (wt, t_rel, duration, src_lang, tgt_lang, src_text))
            eid = cur.lastrowid
            apath = None
            if audio is not None and self.keep_audio and len(audio):
                apath = str(self.audio_dir / f"{eid}.wav")
                write_wav(apath, audio, self.sample_rate)
                self._db.execute("UPDATE entries SET audio_path=? WHERE id=?", (apath, eid))
            self._db.commit()
            self.trim()
            return Entry(eid, wt, t_rel, duration, src_lang, tgt_lang, src_text, audio_path=apath)

    def update_translation(self, entry_id: int, text: str, provider: str = "", **timings) -> Entry | None:
        with self._lock:
            row = self._db.execute("SELECT timings FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not row:
                return None
            t = json.loads(row[0] or "{}")
            t.update(timings)
            self._db.execute("UPDATE entries SET tgt_text=?, provider=?, timings=? WHERE id=?",
                             (text, provider, json.dumps(t), entry_id))
            self._db.commit()
            return self.get(entry_id)

    def update_timings(self, entry_id: int, **timings) -> None:
        with self._lock:
            row = self._db.execute("SELECT timings FROM entries WHERE id=?", (entry_id,)).fetchone()
            if row:
                t = json.loads(row[0] or "{}")
                t.update(timings)
                self._db.execute("UPDATE entries SET timings=? WHERE id=?", (json.dumps(t), entry_id))
                self._db.commit()

    def trim(self) -> int:
        with self._lock:
            cutoff = self._clock() - self.max_seconds
            old = self._db.execute(
                "SELECT id, audio_path FROM entries WHERE wall_time + duration < ?", (cutoff,)).fetchall()
            for _id, ap in old:
                if ap:
                    try:
                        os.remove(ap)
                    except FileNotFoundError:
                        pass
            if old:
                self._db.execute("DELETE FROM entries WHERE wall_time + duration < ?", (cutoff,))
                self._db.commit()
                live = {r[0] for r in self._db.execute("SELECT audio_path FROM entries WHERE audio_path IS NOT NULL")}
                for p in self.audio_dir.glob("*.wav"):
                    if str(p) not in live:
                        p.unlink(missing_ok=True)
            return len(old)

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM entries")
            self._db.commit()
            for p in self.audio_dir.glob("*.wav"):
                p.unlink(missing_ok=True)
            self.session_start = self._clock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(r) -> Entry:
        return Entry(id=r[0], wall_time=r[1], t_rel=r[2], duration=r[3], src_lang=r[4], tgt_lang=r[5],
                     src_text=r[6] or "", tgt_text=r[7] or "", provider=r[8] or "",
                     timings_ms=json.loads(r[9] or "{}"), audio_path=r[10])

    def get(self, entry_id: int) -> Entry | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            return self._row(r) if r else None

    def entries(self) -> list[Entry]:
        with self._lock:
            self.trim()
            return [self._row(r) for r in self._db.execute("SELECT * FROM entries ORDER BY wall_time, id")]

    def __len__(self) -> int:
        return len(self.entries())

    def audio_seconds(self) -> float:
        return sum(e.duration for e in self.entries())

    def disk_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.audio_dir.glob("*.wav"))

    def export_text(self, path: str | None = None) -> str:
        lines = []
        for e in self.entries():
            lines.append(f"[{e.clock()} | {e.timecode()}] {e.src_lang}: {e.src_text}")
            lines.append(f"{' ' * 19}-> {e.tgt_lang}: {e.tgt_text}")
        text = "\n".join(lines) + ("\n" if lines else "")
        if path:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def export_json(self, path: str | None = None) -> str:
        text = json.dumps([e.to_dict() for e in self.entries()], ensure_ascii=False, indent=2)
        if path:
            Path(path).write_text(text, encoding="utf-8")
        return text
