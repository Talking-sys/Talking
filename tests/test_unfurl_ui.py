"""Playwright smoke: preview card + sandboxed mini-browser. Skips if no browser."""
from __future__ import annotations

import os
import socket
import tempfile
import threading

import pytest

pytestmark = pytest.mark.timeout(60)

pytest.importorskip("playwright")

playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_url():
    import uvicorn

    from talking.config import Settings
    from talking.engine import Engine
    from talking.server import create_app
    from talking.stt import FakeSTTEngine
    from talking.translate import StubProvider, Translator
    from talking.unfurl.service import UnfurlService

    os.environ.setdefault("APP_TRANSLATE_PROVIDERS", "stub")
    os.environ["UNFURL_STUB"] = "1"
    os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="talking-unfurl-ui-")
    port = _free_port()
    s = Settings()
    tr = Translator(s, providers=[StubProvider()])
    fake = FakeSTTEngine(s, default_text="hello", default_lang="en")
    eng = Engine(s, translator=tr, stt=fake, start_background=False)
    app = create_app(s, eng, unfurl=UnfurlService(stub=True))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            t.join(0.05)
    else:
        pytest.skip("uvicorn did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_preview_card_and_minibrowser(live_url):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright not installed")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            pytest.skip("chromium not installed for playwright")
        page = browser.new_page()
        page.goto(live_url, wait_until="domcontentloaded")
        page.fill("#chatInput", "смотрите https://example.com/story")
        page.click("#chatForm button[type=submit]")
        page.wait_for_selector(".link-card")
        assert page.locator(".link-card .title").first.inner_text()
        assert page.locator(".link-card .site").first.inner_text()
        page.locator(".link-card").first.click()
        page.wait_for_selector("#miniBrowser:not([hidden])")
        frame = page.locator("#mbFrame")
        assert frame.get_attribute("sandbox") == ""
        src = frame.get_attribute("src") or ""
        assert src.startswith("http")
        assert "javascript:" not in src.lower()
        assert "data:" not in src.lower()
        assert page.locator("#mbUrl").inner_text()
        heading = page.frame_locator("#mbFrame").locator("h1")
        assert "link preview" in heading.inner_text().lower()
        page.click("#mbClose")
        assert page.locator("#miniBrowser").get_attribute("hidden") is not None
        browser.close()
