"""Talking game-embed SDK (MIT). Translate text and speech from a game client.

This package talks to a Talking server over HTTP. It does **not** include the
GPL translation engine; closed-source games can depend on this client.
"""

from .client import AsyncTalkingClient, TalkingClient
from .errors import (
    AuthError,
    ConfigError,
    InvalidRequestError,
    NetworkError,
    OfflineError,
    PayloadTooLargeError,
    RateLimitError,
    ServiceUnavailableError,
    TalkingError,
    TalkingTimeoutError,
    UnsupportedLanguageError,
)
from .models import Language, StreamEvent, Translation

__version__ = "0.1.0"
__all__ = [
    "AsyncTalkingClient",
    "AuthError",
    "ConfigError",
    "InvalidRequestError",
    "Language",
    "NetworkError",
    "OfflineError",
    "PayloadTooLargeError",
    "RateLimitError",
    "ServiceUnavailableError",
    "StreamEvent",
    "TalkingClient",
    "TalkingError",
    "TalkingTimeoutError",
    "Translation",
    "UnsupportedLanguageError",
]
