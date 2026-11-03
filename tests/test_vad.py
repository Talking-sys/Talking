import numpy as np

from talking.vad import VADSegmenter, pcm16_to_float


def test_vad_pause_cut_and_reset():
    sr = 16000
    tone = (0.3 * np.sin(np.arange(int(sr * 0.6)) * 2 * np.pi * 220 / sr)).astype(np.float32)

    def sil(s):
        return np.zeros(int(sr * s), np.float32)

    v = VADSegmenter(sample_rate=sr, silence_ms=800)
    segs = []
    for chunk in (sil(0.5), tone, sil(0.4), tone, sil(1.2), tone, sil(1.2)):
        for i in range(0, len(chunk), 480):
            segs += v.feed(chunk[i:i + 480])
    assert len(segs) == 2


def test_pcm16_roundtrip():
    samples = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)
    from talking.vad import float_to_pcm16
    back = pcm16_to_float(float_to_pcm16(samples))
    assert abs(float(back[1]) - 0.5) < 0.01
