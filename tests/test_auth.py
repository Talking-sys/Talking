"""M10 stages 0–1: sessions, CSRF, local-vs-accounts, sockets."""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

ACCOUNTS = {
    "AUTH_MODE": "accounts",
    "APP_SECRET_KEY": "test-secret-key-not-for-production-use!",
    "LOG_SALT": "test-log-salt-not-a-real-salt",
    "PUBLIC_ORIGIN": "https://testserver",
    "SMS_PROVIDER": "console",
    "DB_ENCRYPTION_KEY": "test-db-encryption-key-not-secret",
    "APP_ENV": "test",
}


def _client(make_client, **extra):
    env = dict(ACCOUNTS)
    env.update(extra)
    return make_client(env=env, base_url="https://testserver")


def _h(csrf=None, origin="https://testserver"):
    h = {}
    if origin:
        h["Origin"] = origin
    if csrf:
        h["X-CSRF-Token"] = csrf
    return h


def _phone_e164(region="US") -> str:
    import phonenumbers
    from phonenumbers import PhoneNumberFormat
    n = phonenumbers.example_number(region)
    return phonenumbers.format_number(n, PhoneNumberFormat.E164)


def _session(c, region="US"):
    """Create a signed-in phone user. Unauthenticated clients are not users."""
    phone = _phone_e164(region)
    start = c.post("/api/auth/phone/start", json={"phone": phone}, headers=_h())
    assert start.status_code == 200, start.text
    r = c.post(
        "/api/auth/phone/verify",
        json={"phone": phone, "code": start.json()["debug_code"]},
        headers=_h(),
    )
    assert r.status_code == 200, r.text
    return r


def test_local_auth_refused_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_SECRET_KEY", "k" * 32)
    monkeypatch.setenv("LOG_SALT", "s" * 32)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="AUTH_MODE=local"):
        create_app(Settings())


def test_secret_required_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "accounts")
    monkeypatch.setenv("APP_SECRET_KEY", "")
    monkeypatch.setenv("LOG_SALT", "s" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://talking:talking@localhost:5432/talking")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        create_app(Settings())


def _production_accounts_env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "accounts")
    monkeypatch.setenv("APP_SECRET_KEY", "k" * 32)
    monkeypatch.setenv("LOG_SALT", "s" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://talking:talking@localhost:5432/talking")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    for key, value in extra.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_production_refuses_console_sms_provider(monkeypatch, tmp_path):
    _production_accounts_env(monkeypatch, tmp_path, SMS_PROVIDER="console")
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="SMS_PROVIDER"):
        create_app(Settings())


def test_production_refuses_unset_sms_provider(monkeypatch, tmp_path):
    _production_accounts_env(monkeypatch, tmp_path, SMS_PROVIDER=None)
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="SMS_PROVIDER"):
        create_app(Settings())


def test_production_refuses_twilio_without_credentials(monkeypatch, tmp_path):
    _production_accounts_env(monkeypatch, tmp_path, SMS_PROVIDER="twilio")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_VERIFY_SERVICE_SID", raising=False)
    from talking.config import Settings
    from talking.server import create_app
    with pytest.raises(RuntimeError, match="TWILIO"):
        create_app(Settings())


def test_production_accepts_twilio_verify(monkeypatch, tmp_path):
    _production_accounts_env(
        monkeypatch, tmp_path,
        SMS_PROVIDER="twilio",
        TWILIO_ACCOUNT_SID="ACtest",
        TWILIO_AUTH_TOKEN="token",
        TWILIO_VERIFY_SERVICE_SID="VAtest",
    )
    from talking.auth.http import validate_auth_settings
    from talking.auth.providers import build_phone_provider
    from talking.config import Settings
    settings = Settings()
    validate_auth_settings(settings)
    assert build_phone_provider(settings).name == "twilio"


def test_twilio_incomplete_does_not_fallback_to_console(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_VERIFY_SERVICE_SID", raising=False)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    from talking.auth.providers import build_phone_provider
    from talking.config import Settings
    with pytest.raises(RuntimeError, match="TWILIO"):
        build_phone_provider(Settings())


def test_anonymous_endpoint_gone(make_client):
    c = _client(make_client)
    r = c.post("/api/auth/anonymous", headers=_h())
    assert r.status_code == 404
    me = c.get("/api/auth/me")
    assert me.status_code == 401


def test_refresh_token_reuse_revokes_family(make_client):
    from fastapi.testclient import TestClient

    c = _client(make_client)
    r = _session(c)
    csrf = r.json()["csrf_token"]
    refresh = r.cookies.get("refresh") or c.cookies.get("refresh")
    assert refresh
    rot = c.post("/api/auth/refresh", headers=_h(csrf))
    assert rot.status_code == 200, rot.text
    c2 = TestClient(c.app_, base_url="https://testserver")
    reused = c2.post(
        "/api/auth/refresh",
        headers={**_h(), "Cookie": f"refresh={refresh}; __Secure-refresh={refresh}"},
    )
    assert reused.status_code == 401
    assert reused.json()["detail"] == "refresh_reuse"
    me = c.get("/api/auth/me")
    assert me.status_code == 401


def test_csrf_rejection(make_client):
    c = _client(make_client)
    r = _session(c)
    csrf = r.json()["csrf_token"]
    bad_origin = c.post("/api/auth/logout", headers=_h(csrf, origin="https://evil.example"))
    assert bad_origin.status_code == 403
    missing = c.post("/api/auth/logout", headers=_h(origin="https://testserver"))
    assert missing.status_code == 403
    empty_origin = c.post("/api/auth/logout", headers=_h(csrf, origin=""))
    assert empty_origin.status_code == 403
    assert empty_origin.json()["detail"] == "csrf origin"
    ok = c.post("/api/auth/logout", headers=_h(csrf))
    assert ok.status_code == 200


def test_socket_without_cookie_closed_in_accounts_mode(make_client):
    c = _client(make_client)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/chat"):
            pass


def test_socket_without_token_closed_in_local_mode(make_client):
    c = make_client()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/chat"):
            pass


def test_logout_all_revokes_sockets(make_client):
    c = _client(make_client)
    r = _session(c)
    csrf = r.json()["csrf_token"]
    tok = c.get("/api/ws-token").json()["token"]
    with c.websocket_connect("/ws/chat?t=" + tok) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
        out = c.post("/api/auth/logout-all", headers=_h(csrf))
        assert out.status_code == 200
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_token_requires_session_and_is_short_lived(make_client):
    c = _client(make_client)
    denied = c.get("/api/ws-token")
    assert denied.status_code == 401
    r = _session(c)
    tok = c.get("/api/ws-token")
    assert tok.status_code == 200
    assert tok.json()["expires_in"] == 60
    with c.websocket_connect("/ws/chat?t=" + tok.json()["token"]) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "chat.hello"
    c.post("/api/auth/logout-all", headers=_h(r.json()["csrf_token"]))


def test_sessions_list_and_revoke(make_client):
    c = _client(make_client)
    r = _session(c)
    csrf = r.json()["csrf_token"]
    listed = c.get("/api/auth/sessions")
    assert listed.status_code == 200
    rows = listed.json()["sessions"]
    assert len(rows) >= 1
    assert any(s["current"] for s in rows)
    sid = rows[0]["id"]
    deleted = c.delete(f"/api/auth/sessions/{sid}", headers=_h(csrf))
    assert deleted.status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_local_mode_still_issues_ws_token(make_client):
    c = make_client()
    r = c.get("/api/ws-token")
    assert r.status_code == 200
    assert r.json()["token"]
    me = c.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == "local"
    assert me.json()["auth_mode"] == "local"
    assert me.json()["anonymous"] is False
