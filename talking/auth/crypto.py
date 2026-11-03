"""Hashing, HMAC identifiers, and optional AES-GCM for encrypted PII.

Never log inputs to these helpers (phones, emails, codes, tokens).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ACCESS_TTL_S = 15 * 60
REFRESH_TTL_S = 30 * 24 * 3600
RECOVERY_TTL_S = 0  # recovery codes do not expire; they are single-use
PHONE_OTP_TTL_S = 10 * 60
WEBAUTHN_CHALLENGE_TTL_S = 2 * 60
WEBAUTHN_TIMEOUT_MS = 60_000
WS_TOKEN_TTL_S = 60
WS_TOKEN_TTL_LOCAL_S = 3600
FRESH_AUTH_S = 10 * 60
CSRF_TTL_S = 30 * 24 * 3600

ACCESS_COOKIE = "__Host-access"
REFRESH_COOKIE = "__Secure-refresh"
ACCESS_COOKIE_DEV = "access"
REFRESH_COOKIE_DEV = "refresh"
CSRF_HEADER = "X-CSRF-Token"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    import uuid
    return str(uuid.uuid4())


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hmac_hex(secret: bytes, value: str, *, n: int = 32) -> str:
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:n]


def phone_subject(secret: bytes, phone_e164: str) -> str:
    return hmac.new(secret, f"phone:{phone_e164}".encode("utf-8"), hashlib.sha256).hexdigest()


def passkey_subject(secret: bytes, credential_id: bytes) -> str:
    """HMAC of the credential id for identities.subject (never store the raw id there)."""
    return hmac.new(secret, b"passkey:" + credential_id, hashlib.sha256).hexdigest()


def ip_hash(secret: bytes, ip: str) -> str:
    return hmac_hex(secret, f"ip:{ip}", n=32)


def user_log_id(secret: bytes, user_id: str) -> str:
    """Opaque id for logs. Never the raw user_id."""
    return hmac_hex(secret, f"uid:{user_id}", n=16)


def load_encryption_key(raw: str, fallback_secret: bytes | None = None) -> bytes | None:
    if raw:
        raw = raw.strip()
        if len(raw) == 64:
            try:
                return bytes.fromhex(raw)
            except ValueError:
                pass
        return hashlib.sha256(raw.encode("utf-8")).digest()
    if fallback_secret:
        return hashlib.sha256(b"enc:" + fallback_secret).digest()
    return None


def encrypt_pii(key: bytes | None, plaintext: str | None) -> str | None:
    if not plaintext or not key:
        return None
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), b"talking-pii")
    return (nonce + ct).hex()


def decrypt_pii(key: bytes | None, blob: str | None) -> str | None:
    if not blob or not key:
        return None
    try:
        raw = bytes.fromhex(blob)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(key).decrypt(nonce, ct, b"talking-pii").decode("utf-8")
    except Exception:
        return None


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def new_recovery_code() -> str:
    raw = secrets.token_hex(10)
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}"


def new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
