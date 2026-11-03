"""Orchestrate URL unfurl: fetch → parse → proxy image → cache."""
from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

from .cache import DEFAULT_TTL, FAILURE_TTL, PreviewCache
from .fetch import MAX_HTML_BYTES, MAX_IMAGE_BYTES, FetchError, SafeFetcher
from .images import make_stub_jpeg, reencode_preview
from .parser import TITLE_MAX, decode_html, parse_html, parse_oembed
from .ssrf import SSRFError, normalize_url
from .urls import extract_urls

log = logging.getLogger(__name__)

PRIVACY_NOTE = (
    "Talking servers fetch publicly available page metadata (Open Graph, Twitter Card, "
    "oEmbed, title) to build this preview. The destination sees Talking's server IP, "
    "not yours. Cookies are never sent. Preview images are re-encoded here so other "
    "clients do not hotlink the origin (no IP leak). You can open the page in a "
    "sandboxed mini-browser or in an external browser."
)

STUB_BROWSE_PATH = "/static/fixtures/sample-article.html"


def _image_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]


def _site_from_url(url: str) -> str:
    host = (urlparse(url).hostname or url).lower().removeprefix("www.")
    return host[:80]


def _display_url(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").removeprefix("www.")
    path = p.path if p.path != "/" else ""
    return (host + path)[:120]


def domain_fallback(url: str, original: str | None = None) -> dict:
    src = original or url
    try:
        canon = normalize_url(src)
    except SSRFError:
        canon = src
    site = _site_from_url(canon)
    return {
        "ok": True,
        "url": canon,
        "display_url": _display_url(canon),
        "site_name": site,
        "title": site,
        "description": "",
        "image_id": None,
        "image_path": None,
        "browse_url": canon,
        "fallback": True,
        "source": "domain",
        "privacy": PRIVACY_NOTE,
    }


def _public_preview(d: dict) -> dict:
    """Drop internal-only keys before going over the wire."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


class UnfurlService:
    def __init__(
        self,
        *,
        redis_url: str = "",
        cache: PreviewCache | None = None,
        fetcher: SafeFetcher | None = None,
        stub: bool = False,
        cache_ttl: int = DEFAULT_TTL,
        failure_ttl: int = FAILURE_TTL,
        timeout: float = 8.0,
        max_html_bytes: int = MAX_HTML_BYTES,
        max_image_bytes: int = MAX_IMAGE_BYTES,
        max_redirects: int = 4,
    ):
        self.cache = cache or PreviewCache(redis_url)
        self.fetcher = fetcher or SafeFetcher(
            timeout=timeout,
            max_redirects=max_redirects,
            max_bytes=max_html_bytes,
        )
        self.stub = stub
        self.cache_ttl = cache_ttl
        self.failure_ttl = failure_ttl
        self.max_image_bytes = max_image_bytes
        if stub:
            jpeg = make_stub_jpeg("Talking preview")
            self.cache.set_bytes("img:stub", jpeg, cache_ttl)

    def preview(self, url: str) -> dict:
        try:
            canon = normalize_url(url)
        except SSRFError:
            raise
        ck = "p:" + _image_id(canon)
        cached = self.cache.get_json(ck)
        if cached:
            return _public_preview(cached)
        if self.stub:
            body = self._stub_preview(canon)
            self.cache.set_json(ck, body, self.cache_ttl)
            return _public_preview(body)
        try:
            body = self._fetch_preview(canon)
            ttl = self.failure_ttl if body.get("fallback") else self.cache_ttl
            self.cache.set_json(ck, body, ttl)
            return _public_preview(body)
        except SSRFError:
            raise
        except FetchError:
            fb = domain_fallback(canon)
            self.cache.set_json(ck, fb, self.failure_ttl)
            return _public_preview(fb)
        except Exception:
            log.exception("unfurl failed for host=%s", _site_from_url(canon))
            fb = domain_fallback(canon)
            self.cache.set_json(ck, fb, self.failure_ttl)
            return _public_preview(fb)

    def image_bytes(self, image_id: str) -> bytes | None:
        if not image_id or len(image_id) > 64 or "/" in image_id:
            return None
        return self.cache.get_bytes("img:" + image_id)

    def _stub_preview(self, canon: str) -> dict:
        site = _site_from_url(canon)
        return {
            "ok": True,
            "url": canon,
            "display_url": _display_url(canon),
            "site_name": site or "example.com",
            "title": "Talking — link preview",
            "description": "Sandboxed mini-browser demo. Title, description and a re-encoded picture, Telegram-style.",
            "image_id": "stub",
            "image_path": "/api/unfurl/image/stub",
            "browse_url": STUB_BROWSE_PATH,
            "fallback": False,
            "source": "opengraph",
            "privacy": PRIVACY_NOTE,
        }

    def _fetch_preview(self, canon: str) -> dict:
        resp = self.fetcher.get(canon)
        if resp.status >= 400:
            return domain_fallback(canon)
        html = decode_html(resp.body, resp.headers.get("content-type", ""))
        parsed = parse_html(html, resp.url or canon)
        if parsed.get("oembed_url"):
            try:
                oem = self.fetcher.get(
                    parsed["oembed_url"],
                    max_bytes=65_536,
                    allowed_content_types=("application/json", "text/json", "application/javascript"),
                    accept="application/json, text/javascript;q=0.5",
                )
                extra = parse_oembed(oem.body, oem.headers.get("content-type", ""))
                if extra.get("title") and not parsed.get("title"):
                    parsed["title"] = extra["title"]
                if extra.get("site_name") and not parsed.get("site_name"):
                    parsed["site_name"] = extra["site_name"]
                if extra.get("image_url") and not parsed.get("image_url"):
                    parsed["image_url"] = extra["image_url"]
                    parsed["source"] = "oembed"
            except SSRFError:
                pass
            except Exception:
                log.debug("oembed skipped", exc_info=True)
        site = parsed.get("site_name") or _site_from_url(canon)
        title = parsed.get("title") or site
        desc = parsed.get("description") or ""
        image_id = None
        if parsed.get("image_url"):
            image_id = self._proxy_image(parsed["image_url"])
        fallback = not (parsed.get("title") or parsed.get("description") or image_id)
        if fallback:
            return domain_fallback(canon)
        return {
            "ok": True,
            "url": parsed.get("canonical_url") or canon,
            "display_url": _display_url(canon),
            "site_name": site[:80],
            "title": title[:TITLE_MAX],
            "description": desc,
            "image_id": image_id,
            "image_path": f"/api/unfurl/image/{image_id}" if image_id else None,
            "browse_url": parsed.get("canonical_url") or canon,
            "fallback": False,
            "source": parsed.get("source") or "html",
            "privacy": PRIVACY_NOTE,
        }

    def _proxy_image(self, image_url: str) -> str | None:
        try:
            normalize_url(image_url)
        except SSRFError:
            return None
        iid = _image_id(image_url)
        existing = self.cache.get_bytes("img:" + iid)
        if existing:
            return iid
        try:
            resp = self.fetcher.get_image(image_url)
            if resp.status >= 400 or not resp.body:
                return None
            jpeg = reencode_preview(resp.body)
        except SSRFError:
            return None
        except Exception:
            log.debug("image proxy failed", exc_info=True)
            return None
        self.cache.set_bytes("img:" + iid, jpeg, self.cache_ttl)
        return iid


__all__ = [
    "UnfurlService",
    "PRIVACY_NOTE",
    "domain_fallback",
    "extract_urls",
]
