import numpy as np

from talking.config import Settings
from talking.stt import FakeSTTEngine, STTUnavailable
from talking.translate import StubProvider, Translator
from talking.voice_translate.pipeline import resolve_direction, swap_pair, transcribe_and_translate
from talking.voice_translate.stream_stt import WindowedWhisperSTT


def test_resolve_auto_a_then_b():
    a = resolve_direction("ru", "auto", "en", "ru")
    assert a.src == "ru" and a.tgt == "en" and a.speaker == "a"
    b = resolve_direction("en", "auto", "en", "ru")
    assert b.src == "en" and b.tgt == "ru" and b.speaker == "b"


def test_resolve_forced_source():
    d = resolve_direction("fr", "de", "en", "de")
    assert d.src == "de" and d.tgt == "en" and d.speaker == "a"
    back = resolve_direction("en", "de", "en", "de")
    assert back.speaker == "b" and back.tgt == "de"


def test_swap_pair_auto_and_fixed():
    assert swap_pair("auto", "en", "ru") == ("auto", "ru", "en")
    assert swap_pair("ru", "en", "ru") == ("en", "ru", "ru")


def test_transcribe_and_translate_stub():
    s = Settings()
    eng = type("E", (), {})()
    eng.stt = FakeSTTEngine(s, default_text="hello", default_lang="en")
    eng.translator = Translator(s, providers=[StubProvider()])
    audio = np.zeros(16000, dtype=np.float32)
    r = transcribe_and_translate(eng, audio, source_lang="auto", target_lang="ru", partner_lang="en")
    assert r.text == "hello"
    assert r.translation == "[en→ru] hello"
    assert r.provider == "stub"
    assert r.speaker == "a"


def test_windowed_whisper_emits_partial_then_final():
    s = Settings()
    fake = FakeSTTEngine(s)
    fake.push("hel", "en")
    fake.push("hello world", "en")
    stt = WindowedWhisperSTT(
        fake, clock=lambda: 10.0, min_s=0.1, partial_interval_s=0.8, partial_window_s=3.0,
    )
    pcm = np.zeros(int(0.4 * 16000), dtype=np.float32)
    parts = stt.feed(pcm)
    assert len(parts) == 1
    assert parts[0].partial is True
    assert parts[0].text == "hel"
    finals = stt.flush()
    assert len(finals) == 1
    assert finals[0].partial is False
    assert finals[0].text == "hello world"


def test_transcribe_skips_when_not_ready():
    class Dead:
        ready = False

        def transcribe(self, *a, **k):
            raise AssertionError("should not run")

    eng = type("E", (), {})()
    eng.stt = Dead()
    try:
        transcribe_and_translate(eng, np.zeros(1600, dtype=np.float32), load_stt=False)
        raise AssertionError("expected STTUnavailable")
    except STTUnavailable:
        pass
