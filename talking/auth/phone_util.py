"""E.164 normalization and country allowlist. Never log the raw number."""
from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


class PhoneError(ValueError):
    pass


def parse_e164(raw: str, default_region: str = "") -> str:
    text = (raw or "").strip()
    if not text:
        raise PhoneError("phone_required")
    region = (default_region or "").strip().upper() or None
    try:
        num = phonenumbers.parse(text, region)
    except NumberParseException as e:
        raise PhoneError("invalid_phone") from e
    if not phonenumbers.is_valid_number(num):
        raise PhoneError("invalid_phone")
    return phonenumbers.format_number(num, PhoneNumberFormat.E164)


def region_of(phone_e164: str) -> str:
    try:
        num = phonenumbers.parse(phone_e164, None)
        return phonenumbers.region_code_for_number(num) or ""
    except NumberParseException:
        return ""


def country_allowed(phone_e164: str, allowlist: str) -> bool:
    raw = (allowlist or "").strip()
    if not raw:
        return True
    allowed = {p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()}
    if not allowed:
        return True
    return region_of(phone_e164) in allowed
