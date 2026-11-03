"""Compact ES256 JWT encode/verify (Apple client-secret, ID tokens, S2S).

Uses cryptography only (Apache-2.0 OR BSD-3-Clause). Never log tokens.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key


class JwtError(Exception):
    def __init__(self, code: str = "invalid_jwt"):
        super().__init__(code)
        self.code = code


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception as exc:
        raise JwtError("invalid_jwt") from exc


def load_ec_private_key(pem: str):
    blob = (pem or "").replace("\\n", "\n").strip().encode("utf-8")
    if not blob:
        raise JwtError("invalid_key")
    try:
        key = load_pem_private_key(blob, password=None)
    except Exception as exc:
        raise JwtError("invalid_key") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise JwtError("invalid_key")
    if not isinstance(key.curve, ec.SECP256R1):
        raise JwtError("invalid_key")
    return key


def public_jwk(private_key, kid: str) -> dict[str, str]:
    pub = private_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
        "x": b64url_encode(pub.x.to_bytes(32, "big")),
        "y": b64url_encode(pub.y.to_bytes(32, "big")),
    }


def jwk_to_public_key(jwk: dict):
    try:
        x = int.from_bytes(b64url_decode(str(jwk["x"])), "big")
        y = int.from_bytes(b64url_decode(str(jwk["y"])), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    except Exception as exc:
        raise JwtError("invalid_jwt") from exc


def encode_es256(payload: dict[str, Any], private_key, *, kid: str | None = None) -> str:
    header: dict[str, Any] = {"alg": "ES256"}
    if kid:
        header["kid"] = kid
    signing_input = (
        b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
        + "."
        + b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    )
    der = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return signing_input + "." + sig


def _parse_parts(token: str) -> tuple[dict, dict, bytes, bytes]:
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise JwtError("invalid_jwt")
    header_raw, payload_raw, sig_raw = parts
    try:
        header = json.loads(b64url_decode(header_raw))
        payload = json.loads(b64url_decode(payload_raw))
    except Exception as exc:
        raise JwtError("invalid_jwt") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise JwtError("invalid_jwt")
    if header.get("alg") != "ES256":
        raise JwtError("invalid_jwt")
    sig = b64url_decode(sig_raw)
    if len(sig) != 64:
        raise JwtError("invalid_jwt")
    signing_input = f"{header_raw}.{payload_raw}".encode("ascii")
    return header, payload, sig, signing_input


def verify_es256(
    token: str,
    keys: list[dict] | dict,
    *,
    audience: str,
    issuer: str,
    skew_s: int = 60,
    now: int | None = None,
) -> dict[str, Any]:
    header, payload, sig, signing_input = _parse_parts(token)
    key_list = keys if isinstance(keys, list) else (keys.get("keys") or [])
    kid = header.get("kid")
    chosen = None
    for jwk in key_list:
        if kid and jwk.get("kid") and jwk.get("kid") != kid:
            continue
        if jwk.get("kty") != "EC":
            continue
        chosen = jwk
        if kid and jwk.get("kid") == kid:
            break
    if chosen is None:
        raise JwtError("invalid_jwt")
    public = jwk_to_public_key(chosen)
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    try:
        public.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception as exc:
        raise JwtError("invalid_jwt") from exc
    ts = int(now if now is not None else time.time())
    exp = payload.get("exp")
    if exp is None or ts > int(exp) + skew_s:
        raise JwtError("invalid_jwt")
    iat = payload.get("iat")
    if iat is not None and int(iat) - skew_s > ts:
        raise JwtError("invalid_jwt")
    nbf = payload.get("nbf")
    if nbf is not None and ts + skew_s < int(nbf):
        raise JwtError("invalid_jwt")
    iss = payload.get("iss")
    if iss != issuer:
        raise JwtError("invalid_jwt")
    aud = payload.get("aud")
    if isinstance(aud, list):
        if audience not in aud:
            raise JwtError("invalid_jwt")
    elif aud != audience:
        raise JwtError("invalid_jwt")
    return payload
