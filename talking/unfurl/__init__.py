"""Server-side link unfurl (Open Graph / Twitter / oEmbed) with SSRF guards.

This package is independent of full chat (ROADMAP M4). A later chat PR can
import UnfurlService without rewriting fetch/parser/SSRF.
"""
from .cache import PreviewCache
from .service import PRIVACY_NOTE, UnfurlService, domain_fallback
from .ssrf import SSRFError, is_blocked_ip, normalize_url
from .urls import extract_urls

__all__ = [
    "UnfurlService",
    "PreviewCache",
    "SSRFError",
    "PRIVACY_NOTE",
    "extract_urls",
    "normalize_url",
    "is_blocked_ip",
    "domain_fallback",
]
