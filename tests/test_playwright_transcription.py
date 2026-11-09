"""Browser smoke for premium on-demand transcription UI."""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.media_bytes import make_wav_bytes

ARTIFACTS = Path(os.environ.get("TALKING_ARTIFACTS") or (Path(__file__).resolve().parent / "output"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(app):
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline and not getattr(server, "started", False):
        time.sleep(0.05)
    if not getattr(server, "started", False):
        pytest.skip("uvicorn did not start")
    return server, port


@pytest.mark.timeout(90)
def test_playwright_transcribe_premium_and_locked(make_client):
    pw = pytest.importorskip("playwright.sync_api")
    premium = make_client(env={"COMMERCIAL_DEV_ENTITLEMENTS": "voice_transcription"})
    premium.stt.push("hello from the voice note", "en")
    d = premium.get("/api/chat/dialogs").json()[0]["id"]
    wav = make_wav_bytes()
    posted = premium.post(
        f"/api/chat/dialogs/{d}/notes",
        files={"file": ("v.wav", wav, "audio/wav")},
        data={"kind": "voice_note", "duration_ms": "800", "target_lang": "ru",
              "waveform": "[12,40,80,40,12,60,90,30]"},
    )
    assert posted.status_code == 201, posted.text

    free = make_client(env={"COMMERCIAL_DEV_ENTITLEMENTS": ""})
    free_d = free.get("/api/chat/dialogs").json()[0]["id"]
    free.post(
        f"/api/chat/dialogs/{free_d}/notes",
        files={"file": ("v.wav", wav, "audio/wav")},
        data={"kind": "voice_note", "duration_ms": "800", "target_lang": "ru",
              "waveform": "[12,40,80,40]"},
    )

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    tests_shot = Path(__file__).resolve().parent / "output"
    tests_shot.mkdir(exist_ok=True)

    server, port = _serve(premium.app_)
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
            except Exception as e:
                pytest.skip(f"chromium not available: {e}")
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{port}/chat", wait_until="domcontentloaded")
            page.wait_for_selector(".wave-note")
            page.wait_for_selector(".transcribe-btn")
            btn = page.locator(".transcribe-btn").first
            assert "is-locked" not in (btn.get_attribute("class") or "")
            feed = ARTIFACTS / "voice_transcription_button_premium.png"
            page.screenshot(path=str(feed), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_transcription_button_premium.png"), full_page=True)
            btn.click()
            page.wait_for_selector(".transcript-panel", timeout=8000)
            assert page.get_by_text("hello from the voice note", exact=False).count() >= 1
            page.locator(".copy-transcript").first.click()
            page.locator("#uiLangEn").click()
            page.wait_for_timeout(200)
            assert page.get_by_text("Copy", exact=False).count() >= 1 or page.get_by_text("Copied", exact=False).count() >= 1
            done = ARTIFACTS / "voice_transcription_expanded_en.png"
            page.screenshot(path=str(done), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_transcription_expanded_en.png"), full_page=True)
            browser.close()
        assert feed.is_file() and done.is_file()
    finally:
        server.should_exit = True

    server2, port2 = _serve(free.app_)
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                pytest.skip(f"chromium not available: {e}")
            page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            page.goto(f"http://127.0.0.1:{port2}/chat", wait_until="domcontentloaded")
            page.wait_for_selector(".transcribe-btn")
            locked = page.locator(".transcribe-btn.is-locked")
            assert locked.count() >= 1
            locked.first.click(force=True)
            page.wait_for_timeout(400)
            shot = ARTIFACTS / "voice_transcription_locked_upsell.png"
            page.screenshot(path=str(shot), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_transcription_locked_upsell.png"), full_page=True)
            browser.close()
        assert shot.is_file()
    finally:
        server2.should_exit = True
