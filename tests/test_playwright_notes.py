"""Browser smoke for Telegram-style notes. Needs Chromium + fake media devices."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.media_bytes import make_wav_bytes, webm_header

ARTIFACTS = Path(os.environ.get("TALKING_ARTIFACTS", "/opt/cursor/artifacts"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _circle_bytes(tmp: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return webm_header(4096)
    out = tmp / "circle.webm"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x2ad4c6:s=240x240:d=1.2:r=10",
        "-f", "lavfi", "-i", "sine=f=440:d=1.2",
        "-shortest", "-c:v", "libvpx", "-b:v", "200k", "-c:a", "libopus",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)  # nosec B603
    if r.returncode != 0 or not out.is_file():
        alt = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x2ad4c6:s=240x240:d=1.2:r=10",
            "-pix_fmt", "yuv420p", "-c:v", "libvpx", str(out),
        ]
        r = subprocess.run(alt, capture_output=True, timeout=30)  # nosec B603
    if r.returncode != 0 or not out.is_file():
        return webm_header(4096)
    return out.read_bytes()


@pytest.mark.timeout(90)
def test_playwright_notes_smoke(make_client, tmp_path):
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

    d = c.get("/api/chat/dialogs").json()[0]["id"]
    video = _circle_bytes(tmp_path)
    posted = c.post(
        f"/api/chat/dialogs/{d}/notes",
        files={"file": ("circle.webm", video, "video/webm")},
        data={"kind": "video_note", "duration_ms": "1200", "target_lang": "ru"},
    )
    assert posted.status_code == 201, posted.text
    wav = make_wav_bytes()
    c.post(
        f"/api/chat/dialogs/{d}/notes",
        files={"file": ("v.wav", wav, "audio/wav")},
        data={"kind": "voice_note", "duration_ms": "800", "target_lang": "ru",
              "waveform": "[12,40,80,40,12,60,90,30]"},
    )

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{port}/chat"
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
            context.grant_permissions(["camera", "microphone"])
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("#recordBtn")
            page.wait_for_selector(".circle-note")
            assert page.locator("#recordBtn").get_attribute("data-mode") == "voice"
            tests_shot = Path(__file__).resolve().parent / "output"
            tests_shot.mkdir(exist_ok=True)
            feed_shot = ARTIFACTS / "telegram_notes_feed_circle_and_voice.png"
            page.screenshot(path=str(feed_shot), full_page=True)
            page.screenshot(path=str(tests_shot / "telegram_notes_feed_circle_and_voice.png"), full_page=True)

            page.locator("#recordBtn").click()
            page.wait_for_timeout(80)
            assert page.locator("#recordBtn").get_attribute("data-mode") == "video"
            page.locator("#recordBtn").click()
            page.wait_for_timeout(80)
            assert page.locator("#recordBtn").get_attribute("data-mode") == "voice"

            box = page.locator("#recordBtn").bounding_box()
            assert box
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.wait_for_timeout(900)
            voice_shot = ARTIFACTS / "telegram_notes_recording_voice.png"
            page.screenshot(path=str(voice_shot), full_page=True)
            page.screenshot(path=str(tests_shot / "telegram_notes_recording_voice.png"), full_page=True)
            page.mouse.up()
            page.wait_for_timeout(300)

            page.locator("#recordBtn").click()
            page.wait_for_timeout(80)
            assert page.locator("#recordBtn").get_attribute("data-mode") == "video"
            box = page.locator("#recordBtn").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.wait_for_timeout(1100)
            rec_shot = ARTIFACTS / "telegram_notes_recording_circle.png"
            page.screenshot(path=str(rec_shot), full_page=True)
            page.screenshot(path=str(tests_shot / "telegram_notes_recording_circle.png"), full_page=True)
            page.locator("#recordOverlay").wait_for(state="visible", timeout=3000)
            page.mouse.up()
            page.wait_for_timeout(400)
            browser.close()
        assert feed_shot.is_file() and rec_shot.is_file() and voice_shot.is_file()
    finally:
        server.should_exit = True
