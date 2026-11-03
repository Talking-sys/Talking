"""Redact phones, emails, tokens, and codes from log records. Hash user ids."""
from __future__ import annotations

import logging
import re

from .crypto import user_log_id

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+=/]+", re.I)
_CODE = re.compile(r"\b(\d{6})\b")
_RECOVERY = re.compile(r"\b[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}\b", re.I)


class RedactFilter(logging.Filter):
    def __init__(self, salt: bytes | None = None):
        super().__init__()
        self.salt = salt or b""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        msg = _EMAIL.sub("[redacted-email]", msg)
        msg = _PHONE.sub("[redacted-phone]", msg)
        msg = _BEARER.sub(r"\1[redacted]", msg)
        msg = _RECOVERY.sub("[redacted-code]", msg)
        msg = _CODE.sub("[redacted]", msg)
        record.msg = msg
        record.args = ()
        if self.salt and getattr(record, "user_id", None):
            record.user_id = user_log_id(self.salt, str(record.user_id))  # type: ignore[attr-defined]
        return True


def install_auth_log_filter(salt: bytes) -> None:
    filt = RedactFilter(salt)
    for name in ("talking.auth", "talking"):
        logging.getLogger(name).addFilter(filt)
