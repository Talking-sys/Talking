"""SMS / voice verification providers.

Twilio Verify is the production adapter (SMS + voice, OTP + attempt limits
live on Twilio's side, fraud guard). Vonage Verify is documented as the
alternative but not wired in this stage. The console sink is for AUTH_MODE
tests and local development — it never prints the destination number or code.
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
        channel = "call" if channel == "call" else "sms"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    self._url("Verifications"),
                    auth=(self.account_sid, self.auth_token),
                    data={"To": phone_e164, "Channel": channel},
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


def build_phone_provider(settings) -> PhoneProvider:
    name = (getattr(settings, "sms_provider", None) or "console").strip().lower()
    if name in ("twilio", "twilio_verify"):
        sid = getattr(settings, "twilio_account_sid", "") or ""
        token = getattr(settings, "twilio_auth_token", "") or ""
        service = getattr(settings, "twilio_verify_service_sid", "") or ""
        if sid and token and service:
            return TwilioVerifyProvider(sid, token, service)
        log.info("twilio env incomplete; using console SMS sink")
        return ConsoleDevSink()
    if name == "vonage":
        return VonageVerifyProvider()
    return ConsoleDevSink()
