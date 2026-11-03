"""WebAuthn relying-party helpers. No secrets, no attestation blobs, no PII in logs."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .crypto import WEBAUTHN_TIMEOUT_MS, hash_token
from .types import CredentialRecord

PASSKEY_PROVIDER = "passkey"
KIND_REGISTER = "webauthn_register"
KIND_AUTH = "webauthn_auth"


class PasskeyError(Exception):
    def __init__(self, code: str, cloned: bool = False):
        super().__init__(code)
        self.code = code
        self.cloned = cloned


def rp_from_settings(settings, request) -> tuple[str, str]:
    """Return (rp_id, origin) from PUBLIC_ORIGIN, else the request Origin."""
    raw = (getattr(settings, "public_origin", "") or "").strip()
    if not raw:
        raw = (request.headers.get("origin") or "").strip()
    if not raw:
        raw = str(request.base_url).rstrip("/")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    scheme = (parsed.scheme or "").strip().lower()
    if scheme not in ("https", "http") or not host:
        raise PasskeyError("origin_unconfigured")
    if scheme == "http" and host not in ("localhost", "127.0.0.1", "testserver"):
        raise PasskeyError("origin_unconfigured")
    origin = f"{scheme}://{host}"
    if parsed.port:
        origin = f"{origin}:{parsed.port}"
    return host, origin


def user_handle(user_id: str) -> bytes:
    return user_id.encode("utf-8")[:64]


def challenge_hash(challenge: bytes) -> str:
    return hash_token(bytes_to_base64url(challenge))


def _transports(raw: list[str] | None) -> list[AuthenticatorTransport] | None:
    if not raw:
        return None
    out: list[AuthenticatorTransport] = []
    for item in raw:
        try:
            out.append(AuthenticatorTransport(str(item)))
        except ValueError:
            continue
    return out or None


def _descriptors(creds: list[CredentialRecord]) -> list[PublicKeyCredentialDescriptor]:
    rows = []
    for cred in creds:
        rows.append(PublicKeyCredentialDescriptor(
            id=cred.credential_id,
            transports=_transports(cred.transports),
        ))
    return rows


def registration_options(
    *,
    rp_id: str,
    rp_name: str,
    user_id: str,
    user_name: str,
    existing: list[CredentialRecord],
    challenge: bytes,
) -> dict:
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name or "Talking",
        user_id=user_handle(user_id),
        user_name=(user_name or user_id)[:64],
        user_display_name=(user_name or "Talking")[:64],
        challenge=challenge,
        timeout=WEBAUTHN_TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=_descriptors(existing) or None,
    )
    return json.loads(options_to_json(options))


def authentication_options(*, rp_id: str, challenge: bytes) -> dict:
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        timeout=WEBAUTHN_TIMEOUT_MS,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return json.loads(options_to_json(options))


def credential_id_from(body: dict) -> bytes | None:
    raw = body.get("rawId") or body.get("id")
    if not raw or not isinstance(raw, str):
        return None
    try:
        from webauthn.helpers import base64url_to_bytes
        return base64url_to_bytes(raw)
    except Exception:
        return None


def verify_registration(body: dict, *, challenge: bytes, rp_id: str, origin: str):
    try:
        return verify_registration_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=False,
        )
    except InvalidRegistrationResponse as exc:
        raise PasskeyError("invalid_passkey") from exc


def verify_authentication(
    body: dict,
    *,
    challenge: bytes,
    rp_id: str,
    origin: str,
    public_key: bytes,
    sign_count: int,
):
    try:
        return verify_authentication_response(
            credential=body,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
            require_user_verification=False,
        )
    except InvalidAuthenticationResponse as exc:
        msg = str(exc).lower()
        if "sign count" in msg:
            raise PasskeyError("cloned_credential", cloned=True) from exc
        raise PasskeyError("invalid_passkey") from exc


def transports_from(body: dict) -> list[str] | None:
    resp = body.get("response") if isinstance(body.get("response"), dict) else {}
    raw = resp.get("transports") if isinstance(resp, dict) else None
    if not isinstance(raw, list):
        return None
    out = [str(t)[:16] for t in raw if isinstance(t, str)]
    return out[:8] or None
