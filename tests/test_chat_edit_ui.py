"""Playwright smoke: edit mode banner, save, edited label, ArrowUp, Esc."""
from __future__ import annotations

import os
import socket
import tempfile
import threading

import pytest

pytestmark = pytest.mark.timeout(60)

pytest.importorskip("playwright")
pytest.importorskip("playwright.sync_api")


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
    os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="talking-edit-ui-")
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


def test_edit_mode_save_arrow_up_and_escape(live_url):
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
        page.fill("#chatInput", "первое https://example.com/story")
        page.click("#chatForm button[type=submit]")
        page.wait_for_selector(".chat-msg")
        page.wait_for_selector(".link-card")
        assert page.locator("#editBanner").get_attribute("hidden") is not None

        page.locator(".chat-msg-edit").first.click()
        page.wait_for_selector("#chatMenu:not([hidden])")
        page.locator('#chatMenu button[data-action="edit"]').click()
        page.wait_for_selector("#editBanner:not([hidden])")
        assert "первое" in page.locator("#editBannerPreview").inner_text()
        assert page.locator("#editBannerTitle").inner_text() == "Редактирование"
        assert page.locator("#chatSend").inner_text() == "Сохранить"

        page.fill("#chatInput", "исправленный текст без ссылки")
        page.press("#chatInput", "Enter")
        page.wait_for_selector(".chat-edited")
        assert "изменено" in page.locator(".chat-edited").first.inner_text()
        assert "исправленный текст без ссылки" in page.locator(".chat-text").first.inner_text()
        assert page.locator(".link-card").count() == 0
        assert page.locator("#editBanner").get_attribute("hidden") is not None
        assert page.locator("#chatSend").inner_text() == "Отправить"

        page.fill("#chatInput", "")
        page.locator("#chatInput").press("ArrowUp")
        page.wait_for_selector("#editBanner:not([hidden])")
        assert "исправленный текст без ссылки" in page.locator("#editBannerPreview").inner_text()
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('editBanner').hidden")
        assert page.locator("#chatInput").input_value() == ""
        browser.close()
