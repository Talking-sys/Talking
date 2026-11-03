"""SMS / voice verification providers.

Twilio Verify SMS is the production path. Voice call is an explicit user-chosen
fallback only (never the default). Phone codes are not delivered by email.
The console sink is for local development and tests — it never prints the
destination number or code, and is refused when APP_ENV=production.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from .crypto import hash_token, new_otp_code

log = logging.getLogger("talking.auth")


@dataclass
class StartResult:
    ok: bool
    provider: str
    provider_ref: str | None = None
    code_hash: str | None = None
    debug_code: str | None = None
    error: str | None = None


class PhoneProvider(Protocol):
    name: str

    async def start(self, phone_e164: str, channel: str) -> StartResult: ...
    async def check(self, phone_e164: str, code: str, ref: str | None, code_hash: str | None) -> bool: ...


class ConsoleDevSink:
    """Dev/test sink. Stores only a hash of the OTP (via StartResult.code_hash)."""

    name = "console"

    async def start(self, phone_e164: str, channel: str) -> StartResult:
        code = new_otp_code()
        log.info("dev phone verification started channel=%s", channel)
        return StartResult(
            ok=True, provider=self.name, provider_ref="console",
            code_hash=hash_token(code), debug_code=code,
        )

    async def check(self, phone_e164: str, code: str, ref: str | None, code_hash: str | None) -> bool:
        if not code_hash:
            return False
        return hash_token(code) == code_hash


class TwilioVerifyProvider:
    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, service_sid: str, timeout: float = 10.0):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.service_sid = service_sid
        self.timeout = timeout

    def _url(self, tail: str) -> str:
        return f"https://verify.twilio.com/v2/Services/{self.service_sid}/{tail}"

    async def start(self, phone_e164: str, channel: str) -> StartResult:
        # Twilio Verify also supports email; we never send phone codes that way.
        if channel == "call":
            twilio_channel = "call"
        elif channel == "sms":
            twilio_channel = "sms"
        else:
            return StartResult(ok=False, provider=self.name, error="invalid_channel")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    self._url("Verifications"),
                    auth=(self.account_sid, self.auth_token),
                    data={"To": phone_e164, "Channel": twilio_channel},
                )
            if r.status_code >= 400:
                log.warning("twilio verify start status=%s", r.status_code)
                return StartResult(ok=False, provider=self.name, error="provider_error")
            data = r.json()
            return StartResult(ok=True, provider=self.name, provider_ref=str(data.get("sid") or ""))
        except Exception:
            log.warning("twilio verify start failed")
            return StartResult(ok=False, provider=self.name, error="provider_error")

    async def check(self, phone_e164: str, code: str, ref: str | None, code_hash: str | None) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    self._url("VerificationCheck"),
                    auth=(self.account_sid, self.auth_token),
                    data={"To": phone_e164, "Code": code},
                )
            if r.status_code >= 400:
                return False
            data = r.json()
            return str(data.get("status") or "").lower() == "approved"
        except Exception:
            log.warning("twilio verify check failed")
            return False


class VonageVerifyProvider:
    """Placeholder. Use Twilio Verify or the console sink; see docs/AUTH.md."""

    name = "vonage"

    async def start(self, phone_e164: str, channel: str) -> StartResult:
        return StartResult(ok=False, provider=self.name, error="vonage_not_configured")

    async def check(self, phone_e164: str, code: str, ref: str | None, code_hash: str | None) -> bool:
        return False


def sms_provider_name(settings) -> str:
    return (getattr(settings, "sms_provider", None) or "").strip().lower()


def twilio_credentials(settings) -> tuple[str, str, str]:
    sid = (getattr(settings, "twilio_account_sid", None) or "").strip()
    token = (getattr(settings, "twilio_auth_token", None) or "").strip()
    service = (getattr(settings, "twilio_verify_service_sid", None) or "").strip()
    return sid, token, service


def twilio_configured(settings) -> bool:
    sid, token, service = twilio_credentials(settings)
    return bool(sid and token and service)


def build_phone_provider(settings) -> PhoneProvider:
    name = sms_provider_name(settings) or "console"
    production = bool(getattr(settings, "production", False))
    if name in ("twilio", "twilio_verify"):
        if not twilio_configured(settings):
            raise RuntimeError(
                "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_VERIFY_SERVICE_SID "
                "are required when SMS_PROVIDER=twilio"
            )
        sid, token, service = twilio_credentials(settings)
        return TwilioVerifyProvider(sid, token, service)
    if production:
        raise RuntimeError(
            "SMS_PROVIDER=console is refused when APP_ENV=production; "
            "set SMS_PROVIDER=twilio with Twilio Verify credentials"
        )
    if name == "vonage":
        return VonageVerifyProvider()
    return ConsoleDevSink()
