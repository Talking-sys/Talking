"""Typed errors for the Talking game SDK. Messages never include API keys."""
from __future__ import annotations


class TalkingError(Exception):
    """Base error for every SDK failure."""

    def __init__(self, message: str, *, code: str = "error", status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retry_after = retry_after

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


class ConfigError(TalkingError):
    pass


class AuthError(TalkingError):
    pass


class RateLimitError(TalkingError):
    pass


class InvalidRequestError(TalkingError):
    pass


class UnsupportedLanguageError(TalkingError):
    pass


class PayloadTooLargeError(TalkingError):
    pass


class ServiceUnavailableError(TalkingError):
    pass


class TalkingTimeoutError(TalkingError):
    pass


class NetworkError(TalkingError):
    pass


class OfflineError(TalkingError):
    """Raised when the client was constructed with offline=True (or forced)."""


_CODE_MAP: dict[str, type[TalkingError]] = {
    "unauthorized": AuthError,
    "forbidden": AuthError,
    "rate_limited": RateLimitError,
    "invalid_request": InvalidRequestError,
    "unsupported_language": UnsupportedLanguageError,
    "unsupported_format": InvalidRequestError,
    "payload_too_large": PayloadTooLargeError,
    "no_speech": InvalidRequestError,
    "translation_unavailable": ServiceUnavailableError,
    "stt_unavailable": ServiceUnavailableError,
    "sdk_disabled": ServiceUnavailableError,
    "timeout": TalkingTimeoutError,
    "offline": OfflineError,
}


def error_from_payload(status: int, payload: dict | None, retry_after: float | None = None) -> TalkingError:
    payload = payload or {}
    err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    if not isinstance(err, dict):
        err = {}
    code = str(err.get("code") or "error")
    message = str(err.get("message") or f"HTTP {status}")
    ra = err.get("retry_after", retry_after)
    try:
        ra_f = float(ra) if ra is not None else retry_after
    except (TypeError, ValueError):
        ra_f = retry_after
    cls = _CODE_MAP.get(code, TalkingError)
    return cls(message, code=code, status=status, retry_after=ra_f)
