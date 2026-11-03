"""Browser smoke for sign-in, sessions, and linked methods."""
from __future__ import annotations

import os
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from talking.auth.crypto import utcnow
from tests.test_auth import ACCOUNTS

ARTIFACTS = Path(os.environ.get("TALKING_ARTIFACTS", "/opt/cursor/artifacts"))
pytestmark = pytest.mark.timeout(90)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start(make_client, extra=None):
    import uvicorn

    port = _free_port()
    env = dict(ACCOUNTS)
    env["SMS_DEFAULT_REGION"] = "US"
    env["PUBLIC_ORIGIN"] = f"http://127.0.0.1:{port}"
    env.update(extra or {})
    c = make_client(env=env, base_url=f"http://127.0.0.1:{port}")
    app = c.app_
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline and not getattr(server, "started", False):
        time.sleep(0.05)
    if not getattr(server, "started", False):
        pytest.skip("uvicorn did not start")
    return c, app, server, f"http://127.0.0.1:{port}"


def _browser(pw):
    try:
        return pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"chromium not available: {e}")


def _select_us(page):
    page.select_option("#phoneCountry", "US")
    page.fill("#phoneNational", "2015550123")


@pytest.mark.timeout(90)
def test_playwright_phone_signin_rate_limit_and_sessions(make_client):
    pw = pytest.importorskip("playwright.sync_api")
    c, app, server, origin = _start(make_client)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        with pw.sync_playwright() as p:
            browser = _browser(p)
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(origin + "/signin", wait_until="domcontentloaded")
            page.wait_for_selector("#btnSendCode")
            assert page.locator("#codeForm").is_hidden()
            assert page.locator("#recoveryOnce").is_hidden()
            page.screenshot(path=str(ARTIFACTS / "auth_signin_mobile.png"), full_page=True)
            assert page.locator("#oauthGoogle").get_attribute("hidden") is not None
            assert page.locator("#oauthApple").get_attribute("hidden") is not None
            assert page.locator("#oauthEmail").get_attribute("hidden") is not None
            assert page.locator("#oauthPasskey").get_attribute("hidden") is not None
            _select_us(page)
            with page.expect_response("**/api/auth/phone/start") as start_info:
                page.click("#btnSendCode")
            start = start_info.value
            assert start.ok
            code = start.json()["debug_code"]
            page.wait_for_selector("#codeForm:not([hidden])")
            assert page.locator("#btnResend").is_disabled()
            page.click("#btnCallMe")
            page.wait_for_selector("[data-testid=auth-error]:not([hidden])")
            err = page.locator("[data-testid=auth-error]").inner_text()
            assert err
            page.fill("#codeInput", "000000")
            page.click("#btnVerify")
            page.wait_for_selector("[data-testid=auth-error]:not([hidden])")
            page.fill("#codeInput", code)
            page.click("#btnVerify")
            page.wait_for_url("**/account", timeout=8000)
            page.wait_for_selector("[data-testid=session-list] .list-item")
            assert "Это устройство" in page.locator("[data-testid=session-list]").inner_text()
            assert page.locator("[data-testid=method-list] button[data-action=remove-method]").get_attribute("disabled") is not None
            keys = page.evaluate("() => Object.keys(localStorage)")
            assert not any(k.lower() in ("csrf", "csrf_token", "access", "refresh", "token") for k in keys)
            page.screenshot(path=str(ARTIFACTS / "auth_account_mobile.png"), full_page=True)
            page.on("dialog", lambda d: d.accept())
            page.click("#btnLogoutAll")
            page.wait_for_url("**/signin", timeout=8000)
            browser.close()
    finally:
        server.should_exit = True


@pytest.mark.timeout(90)
def test_playwright_anonymous_recovery_methods_and_oauth(make_client):
    pw = pytest.importorskip("playwright.sync_api")
    c, app, server, origin = _start(make_client)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        with pw.sync_playwright() as p:
            browser = _browser(p)
            context = browser.new_context(viewport={"width": 1100, "height": 800})
            page = context.new_page()
            page.goto(origin + "/signin", wait_until="domcontentloaded")
            page.wait_for_selector("#btnAnonymous")
            display = page.locator("#signinGrid").evaluate("el => getComputedStyle(el).display")
            assert display == "grid"
            page.click("#uiLangEn")
            page.wait_for_function("() => document.documentElement.lang === 'en'")
            assert page.locator("#btnAnonymous").inner_text() == "Continue anonymously"
            page.click("#uiLangRu")
            page.click("#btnAnonymous")
            page.wait_for_selector("#recoveryOnce:not([hidden])")
            code = page.locator("#recoveryCode").inner_text().strip()
            assert "-" in code
            page.check("#savedCheck")
            page.click("#btnSavedContinue")
            page.wait_for_url("**/account", timeout=8000)
            page.wait_for_selector("[data-testid=session-list] .list-item")
            assert page.locator("[data-testid=method-list] button[data-action=remove-method]").is_disabled()

            other = browser.new_context()
            page2 = other.new_page()
            page2.goto(origin + "/signin", wait_until="domcontentloaded")
            page2.click("#recoverySummary")
            page2.fill("#recoveryInput", code)
            page2.click("#btnRecover")
            page2.wait_for_url("**/account", timeout=8000)

            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid=session-list] .list-item")
            assert page.locator("[data-testid=session-list] .list-item").count() >= 2
            other_btn = page.locator("[data-testid=session-list] .list-item").filter(
                has_not=page.locator(".badge")
            ).locator("button").first
            other_btn.click()
            page.wait_for_timeout(300)
            assert page.locator("[data-testid=session-list] .list-item").count() == 1

            page.click("#btnAddPhone")
            page.wait_for_selector("#linkPhoneForm:not([hidden])")
            page.select_option("#phoneCountry", "GB")
            page.fill("#phoneNational", "1134960000")
            with page.expect_response("**/api/auth/phone/start") as start_info:
                page.click("#btnSendCode")
            start = start_info.value
            assert start.ok, start.text()
            for ch in app.state.auth_store.challenges.values():
                ch.created_at = utcnow() - timedelta(seconds=61)
            with page.expect_response("**/api/auth/phone/start") as call_info:
                page.click("#btnCallMe")
            call_res = call_info.value
            posted = call_res.request.post_data_json or {}
            assert posted.get("channel") == "call"
            assert call_res.ok, call_res.text()
            sms_code = call_res.json()["debug_code"]
            page.fill("#codeInput", sms_code)
            page.click("#btnVerify")
            page.wait_for_selector("[data-testid=method-list] .list-item")
            page.wait_for_function(
                "() => document.querySelectorAll('[data-testid=method-list] .list-item').length >= 2"
            )
            assert page.locator("[data-testid=method-list] button[data-action=remove-method]").count() >= 2
            assert page.locator("[data-testid=method-list] button[data-action=remove-method]").first.is_enabled()
            keys = page.evaluate("() => Object.keys(localStorage)")
            assert "csrf_token" not in keys
            page.screenshot(path=str(ARTIFACTS / "auth_account_desktop.png"), full_page=True)

            page3 = browser.new_page()
            page3.route("**/api/auth/config", lambda route: route.fulfill(
                status=200, content_type="application/json",
                json={
                    "auth_mode": "accounts",
                    "methods": {
                        "anonymous": True, "recovery": True, "phone": True,
                        "google": True, "apple": True, "email": True, "passkey": True,
                    },
                    "default_region": "US",
                    "country_allowlist": [],
                },
            ))
            page3.goto(origin + "/signin", wait_until="domcontentloaded")
            # already signed in in this browser context → redirect. Use a fresh context.
            page3.close()
            guest = browser.new_context()
            gpage = guest.new_page()
            gpage.route("**/api/auth/config", lambda route: route.fulfill(
                status=200, content_type="application/json",
                json={
                    "auth_mode": "accounts",
                    "methods": {
                        "anonymous": True, "recovery": True, "phone": True,
                        "google": True, "apple": True, "email": True, "passkey": True,
                    },
                    "default_region": "US",
                    "country_allowlist": [],
                },
            ))
            gpage.goto(origin + "/signin", wait_until="domcontentloaded")
            gpage.wait_for_selector("#oauthGoogle:not([hidden])")
            assert gpage.locator("#oauthApple").get_attribute("hidden") is None
            gpage.click("#oauthGoogle")
            gpage.wait_for_selector("[data-testid=auth-error]:not([hidden])")
            assert "ещё не включён" in gpage.locator("[data-testid=auth-error]").inner_text().lower() or \
                "not available" in gpage.locator("[data-testid=auth-error]").inner_text().lower()
            gpage.screenshot(path=str(ARTIFACTS / "auth_signin_oauth.png"), full_page=True)
            guest.close()
            other.close()
            browser.close()
    finally:
        server.should_exit = True
