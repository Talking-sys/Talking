"""Software authenticator for WebAuthn tests (no browser, no TPM)."""
from __future__ import annotations

import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url, encode_cbor
from webauthn.helpers import base64url_to_bytes


class FakeAuthenticator:
    def __init__(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0

    def _cose(self) -> bytes:
        nums = self.private_key.public_key().public_numbers()
        return encode_cbor({
            1: 2,
            3: -7,
            -1: 1,
            -2: nums.x.to_bytes(32, "big"),
            -3: nums.y.to_bytes(32, "big"),
        })

    def create(self, public_key: dict, origin: str, *, sign_count: int = 0) -> dict:
        challenge = base64url_to_bytes(public_key["challenge"])
        rp_id = public_key["rp"]["id"]
        flags = 0x45  # UP | UV | AT
        auth_data = (
            hashlib.sha256(rp_id.encode("utf-8")).digest()
            + bytes([flags])
            + int(sign_count).to_bytes(4, "big")
            + bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose()
        )
        att_obj = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": bytes_to_base64url(challenge),
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client),
                "attestationObject": bytes_to_base64url(att_obj),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(self, public_key: dict, origin: str, user_id: str, *, sign_count: int | None = None) -> dict:
        challenge = base64url_to_bytes(public_key["challenge"])
        rp_id = public_key["rpId"]
        if sign_count is None:
            self.sign_count += 1
            count = self.sign_count
        else:
            count = sign_count
            self.sign_count = sign_count
        flags = 0x05  # UP | UV
        auth_data = (
            hashlib.sha256(rp_id.encode("utf-8")).digest()
            + bytes([flags])
            + int(count).to_bytes(4, "big")
        )
        client = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": bytes_to_base64url(challenge),
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        sig = self.private_key.sign(
            auth_data + hashlib.sha256(client).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(sig),
                "userHandle": bytes_to_base64url(user_id.encode("utf-8")),
            },
            "clientExtensionResults": {},
        }
