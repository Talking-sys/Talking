from talking import __version__


def test_healthz_and_pages(make_client):
    c = make_client()
    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == __version__
    assert body["postgres"]["configured"] is False
    assert body["redis"]["configured"] is False
    assert c.get("/").status_code == 200
    assert "Микрофон" in c.get("/").text
    assert "frame-ancestors 'none'" in c.get("/").headers["content-security-policy"]
    assert c.get("/privacy").status_code == 200
    assert c.get("/terms").status_code == 200
    assert "hateaboutyouone" in c.get("/terms").text


def test_languages_and_settings(make_client):
    c = make_client()
    langs = c.get("/api/languages").json()
    assert any(x["code"] == "de" for x in langs)
    st = c.get("/api/settings").json()
    assert st["pause_min"] == 300 and st["pause_max"] == 3000
    assert "large-v3" in st["whisper_models"]
    r = c.post("/api/settings", json={"pause_ms": 1200, "whisper_model": "small", "target_lang": "de"})
    assert r.status_code == 200
    assert r.json()["pause_ms"] == 1200
    assert r.json()["whisper_model"] == "small"
    assert r.json()["target_lang"] == "de"


def test_chat_and_games_are_placeholders(make_client):
    c = make_client()
    # Notes slice is live; games remain the M8 placeholder.
    chat = c.get("/api/chat")
    assert chat.status_code == 200
    assert chat.json()["ok"] is True
    assert "notes" in chat.json().get("partial", [])
    assert c.get("/api/games").status_code == 501


def test_commercial_stubs(make_client):
    c = make_client()
    st = c.get("/api/commercial/status").json()
    assert st["enabled"] is False
    assert st["mode"] == "stub"
    assert "licensing" in st["modules"]
    lic = c.get("/api/commercial/license").json()
    assert lic["disabled"] is True
    coins = c.get("/api/commercial/talk-coins").json()
    assert coins["resource"] == "talk_coins"
    wh = c.post("/api/commercial/webhook", json={"event": "noop"})
    assert wh.status_code == 200
    assert wh.json()["accepted"] is False


def test_history_roundtrip(make_client):
    c = make_client()
    assert c.get("/api/history").json() == []
    assert c.post("/api/history/clear").json()["ok"] is True
