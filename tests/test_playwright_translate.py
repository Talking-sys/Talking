"""Browser smoke for press-and-hold voice translation. Chromium + fake mic."""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

ARTIFACTS = Path(os.environ.get("TALKING_ARTIFACTS") or (Path(__file__).resolve().parent / "output"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.timeout(90)
def test_playwright_translate_both_modes(make_client):
    pw = pytest.importorskip("playwright.sync_api")
    import uvicorn

    c = make_client()
    app = c.app_
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

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    tests_shot = Path(__file__).resolve().parent / "output"
    tests_shot.mkdir(exist_ok=True)
    url = f"http://127.0.0.1:{port}/translate"
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--autoplay-policy=no-user-gesture-required",
                    ],
                )
            except Exception as e:
                pytest.skip(f"chromium not available: {e}")
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.grant_permissions(["microphone"])
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("#recordBtn")
            page.wait_for_selector('#source option[value="auto"]', timeout=8000)
            assert page.locator("#modeLive").get_attribute("aria-selected") == "true"
            assert page.locator("#source").locator('option[value="auto"]').count() == 1
            assert page.locator("#target option").count() > 10
            page.select_option("#target", "ru")
            idle_live = ARTIFACTS / "voice_translate_live_idle.png"
            page.screenshot(path=str(idle_live), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_translate_live_idle.png"), full_page=True)

            box = page.locator("#recordBtn").bounding_box()
            assert box
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.wait_for_timeout(900)
            page.locator("#recordOverlay").wait_for(state="visible", timeout=3000)
            live_shot = ARTIFACTS / "voice_translate_live_hold.png"
            page.screenshot(path=str(live_shot), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_translate_live_hold.png"), full_page=True)
            page.mouse.up()
            page.wait_for_timeout(400)

            page.locator("#modeRecord").click()
            page.wait_for_timeout(80)
            assert page.locator("#modeRecord").get_attribute("aria-selected") == "true"
            page.locator("#langSwitch").click()
            page.wait_for_timeout(80)
            assert page.locator("#modeRecord").inner_text().lower().find("record") >= 0

            box = page.locator("#recordBtn").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.wait_for_timeout(1100)
            rec_shot = ARTIFACTS / "voice_translate_record_hold.png"
            page.screenshot(path=str(rec_shot), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_translate_record_hold.png"), full_page=True)
            page.locator("#recordOverlay").wait_for(state="visible", timeout=3000)
            page.mouse.up()
            page.wait_for_timeout(500)
            idle = ARTIFACTS / "voice_translate_record_result.png"
            page.screenshot(path=str(idle), full_page=True)
            page.screenshot(path=str(tests_shot / "voice_translate_record_result.png"), full_page=True)
            browser.close()
        assert live_shot.is_file() and rec_shot.is_file()
    finally:
        server.should_exit = True
