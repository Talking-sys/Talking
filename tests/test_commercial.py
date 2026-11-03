from talking.commercial.client import (
    ENTITLEMENT_VOICE_TRANSCRIPTION,
    CommercialClient,
    verify_webhook,
)


def test_disabled_client_never_calls_network():
    c = CommercialClient("", "")
    st = c.status()
    assert st["enabled"] is False
    assert st["mode"] == "stub"
    assert c.license_status("anon-abc").get("disabled") is True
    assert c.talk_coins_balance()["resource"] == "talk_coins"
    assert c.checkout("premium")["disabled"] is True


def test_has_entitlement_stub_fail_closed():
    c = CommercialClient("", "")
    d = c.check_entitlement("anon-abc", ENTITLEMENT_VOICE_TRANSCRIPTION)
    assert d.entitled is False
    assert d.source == "stub"
    assert c.has_entitlement("anon-abc", ENTITLEMENT_VOICE_TRANSCRIPTION) is False


def test_has_entitlement_dev_override_and_premium_flag():
    a = CommercialClient("", "", app_env="development",
                         dev_entitlements=ENTITLEMENT_VOICE_TRANSCRIPTION)
    assert a.has_entitlement("u", ENTITLEMENT_VOICE_TRANSCRIPTION) is True
    b = CommercialClient("", "", app_env="test", dev_premium=True)
    assert b.has_entitlement("u", ENTITLEMENT_VOICE_TRANSCRIPTION) is True


def test_webhook_hmac():
    secret = "test-secret"
    body = b'{"event":"license.revoked"}'
    import hashlib
    import hmac
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook(secret, body, "sha256=" + sig)
    assert not verify_webhook(secret, body, "sha256=" + ("0" * 64))
    assert not verify_webhook("", body, sig)
