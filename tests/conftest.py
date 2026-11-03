import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_TRANSLATE_PROVIDERS", "stub")
os.environ.setdefault("APP_TTS_PROVIDERS", "")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("WHISPER_MODEL", "base")


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from talking.config import Settings
    from talking.engine import Engine
    from talking.server import create_app
    from talking.stt import FakeSTTEngine
    from talking.translate import StubProvider, Translator

    n = {"i": 0}

    def _make(stt=None, translator=None, env=None, unfurl=None, messages=None):
        n["i"] += 1
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / f"data{n['i']}"))
        monkeypatch.setenv("APP_TRANSLATE_PROVIDERS", "stub")
        s = Settings()
        tr = translator or Translator(s, providers=[StubProvider()])
        fake = stt or FakeSTTEngine(s, default_text="hello", default_lang="en")
        eng = Engine(s, translator=tr, stt=fake, start_background=False)
        app = create_app(s, eng, unfurl=unfurl, messages=messages)
        c = TestClient(app)
        c.eng = eng
        c.app_ = app
        c.stt = fake
        c.unfurl = app.state.unfurl
        return c

    return _make
