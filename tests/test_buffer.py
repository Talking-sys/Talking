import time

import numpy as np

from talking.buffer import RollingBuffer
from talking.offline_queue import OfflineQueue


def test_rolling_buffer_trim(tmp_path):
    clock = {"t": 1000.0}

    def now():
        return clock["t"]

    buf = RollingBuffer(tmp_path, max_seconds=5, clock=now, keep_audio=True)
    audio = np.zeros(1600, dtype=np.float32)
    buf.add("ru", "en", "one", 1.0, audio=audio, wall_time=1000.0)
    buf.add("ru", "en", "two", 1.0, audio=audio, wall_time=1002.0)
    assert len(buf) == 2
    clock["t"] = 1007.5
    n = buf.trim()
    assert n == 1
    remaining = buf.entries()
    assert len(remaining) == 1
    assert remaining[0].src_text == "two"
    buf.update_translation(remaining[0].id, "[ru→en] two", "stub", mt=3)
    got = buf.get(remaining[0].id)
    assert got.tgt_text == "[ru→en] two"
    assert got.provider == "stub"
    buf.close()


def test_offline_queue_ring_and_replay(tmp_path):
    q = OfflineQueue(tmp_path, 180)
    for i in range(25):
        q.add_segment(f"t{i}", "ru", "en", i * 10.0, i * 10.0 + 10.0)
    assert q.audio_seconds() <= 180
    assert q.status()["dropped_segments"] > 0
    q.add_command("es")
    q.add_segment("после команды", "ru", "es", 1000, 1005)
    got, cmds = [], []
    n, err = q.replay(
        lambda it: got.append((it["text"], it["tgt_lang"])) or "ok",
        on_command=lambda c: cmds.append(c["tgt_lang"]),
    )
    assert err is None
    assert cmds == ["es"]
    assert got[-1] == ("после команды", "es")
    assert q.pending_segments() == 0


def test_offline_queue_keeps_until_success(tmp_path):
    q = OfflineQueue(tmp_path, 180)
    q.add_segment("keep me", "ru", "en", 0, 1)
    n, err = q.replay(lambda it: (_ for _ in ()).throw(RuntimeError("down")))
    assert n == 0 and err
    assert q.pending_segments() == 1
    n, err = q.replay(lambda it: "ok")
    assert n == 1 and err is None
    assert q.pending_segments() == 0
