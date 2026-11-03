"""Email normalization for passwordless sign-in. Never log the raw address."""
from __future__ import annotations

import re

class EmailError(ValueError):
    pass


DEFAULT_EMAIL_CHANNEL = "both"
ALLOWED_EMAIL_CHANNELS = frozenset({"code", "link", "both"})
EMAIL_CHALLENGE_KINDS = ("email_otp", "email_link")

# Conservative local-part: no quoted strings, no spaces, no routing chars.
_LOCAL_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+$")
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CODE_ALIASES = frozenset({"code", "otp", "email_otp", "one_time", "onetime"})
_LINK_ALIASES = frozenset({"link", "magic", "magic_link", "magiclink", "email_link"})
_BOTH_ALIASES = frozenset({"both", "all", "code_link", "link_code", "code+link", "link+code"})


def normalize_email_channel(raw: str | None) -> str:
    """Default is both (one-time code and magic link in one send)."""
    value = DEFAULT_EMAIL_CHANNEL if raw is None else str(raw)
    value = value.strip().lower().replace(" ", "_").replace("-", "_")
    if value in ("", DEFAULT_EMAIL_CHANNEL) or value in _BOTH_ALIASES:
        return "both"
    if value in _CODE_ALIASES:
        return "code"
    if value in _LINK_ALIASES:
        return "link"
    raise EmailError("invalid_channel")


def parse_email(raw: str) -> str:
    """Lower-case, IDNA domain, reject header-injection and empty labels.

    Plus-tags are kept as distinct addresses (never auto-merged).
    """
    text = (raw or "").strip()
    if not text:
        raise EmailError("email_required")
    if any(ch in text for ch in "\r\n\0\t "):
        raise EmailError("invalid_email")
    if text.count("@") != 1:
        raise EmailError("invalid_email")
    local, _, domain = text.partition("@")
    local = local.strip().lower()
    domain = domain.strip().lower()
    if not local or not domain:
        raise EmailError("invalid_email")
    if len(local) > 64 or len(text) > 254:
        raise EmailError("invalid_email")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise EmailError("invalid_email")
    if not _LOCAL_RE.fullmatch(local):
        raise EmailError("invalid_email")
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError) as e:
        raise EmailError("invalid_email") from e
    if domain.startswith("[") or domain.endswith("]"):
        raise EmailError("invalid_email")
    labels = domain.split(".")
    if len(labels) < 2:
        raise EmailError("invalid_email")
    for label in labels:
        if not label or not _LABEL_RE.fullmatch(label):
            raise EmailError("invalid_email")
    email = f"{local}@{domain}"
    if len(email) > 254:
        raise EmailError("invalid_email")
    return email
