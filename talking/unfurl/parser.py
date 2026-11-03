"""Parse Open Graph, Twitter Card, oEmbed discovery, <title>, and meta description."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

_WS = re.compile(r"\s+")
_CHARSET_META = re.compile(
    rb'(?is)<meta[^>]+charset\s*=\s*[\'"]?\s*([a-zA-Z0-9._-]+)'
)
_HTTP_EQUIV = re.compile(
    rb'(?is)<meta[^>]+http-equiv\s*=\s*[\'"]content-type[\'"][^>]+content\s*=\s*[\'"][^\'"]*charset\s*=\s*([a-zA-Z0-9._-]+)'
)
_CONTENT_TYPE_CHARSET = re.compile(r"charset\s*=\s*([a-zA-Z0-9._-]+)", re.I)

TITLE_MAX = 200
DESC_MAX = 300
SITE_MAX = 80


def _clip(value: str, n: int) -> str:
    value = _WS.sub(" ", (value or "").replace("\x00", "")).strip()
    if len(value) <= n:
        return value
    return value[: n - 1].rstrip() + "…"


def charset_from_content_type(content_type: str) -> str | None:
    m = _CONTENT_TYPE_CHARSET.search(content_type or "")
    return m.group(1).lower() if m else None


def sniff_html_charset(raw: bytes) -> str | None:
    head = raw[:4096]
    m = _CHARSET_META.search(head)
    if m:
        return m.group(1).decode("ascii", "ignore").lower()
    m = _HTTP_EQUIV.search(head)
    if m:
        return m.group(1).decode("ascii", "ignore").lower()
    return None


def decode_html(raw: bytes, content_type: str = "") -> str:
    """Decode HTML bytes. Tries header charset, then meta, then common encodings."""
    candidates: list[str] = []
    for c in (charset_from_content_type(content_type), sniff_html_charset(raw),
              "utf-8", "utf-8-sig", "windows-1251", "windows-1252", "latin-1"):
        if c and c not in candidates:
            candidates.append(c)
    last = ""
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            try:
                last = raw.decode(enc, errors="replace")
            except LookupError:
                continue
    return last or raw.decode("utf-8", errors="replace")


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self._in_title = False
        self.oembed_json: str | None = None
        self.base_href: str | None = None
        self._title_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = {(k or "").lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (d.get("property") or d.get("name") or d.get("itemprop") or "").strip().lower()
            content = d.get("content") or d.get("value") or ""
            if key and content:
                self.metas.append((key, content))
        elif tag == "title" and not self._title_done:
            self._in_title = True
        elif tag == "link":
            rel = (d.get("rel") or "").lower()
            typ = (d.get("type") or "").lower()
            href = d.get("href") or ""
            if href and "alternate" in rel and "json" in typ and "oembed" in typ:
                self.oembed_json = href
        elif tag == "base" and d.get("href"):
            self.base_href = d["href"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def _meta_map(pairs: list[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in pairs:
        if k not in out and v.strip():
            out[k] = v
    return out


def _first(meta: dict[str, str], *keys: str) -> str:
    for k in keys:
        if meta.get(k):
            return meta[k]
    return ""


def _safe_abs(base: str, maybe: str) -> str | None:
    if not maybe:
        return None
    maybe = maybe.strip()
    lower = maybe.lower()
    if lower.startswith(("javascript:", "data:", "blob:", "file:", "vbscript:", "about:")):
        return None
    abs_url = urljoin(base, maybe)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return None
    return abs_url


def parse_html(html: str, page_url: str) -> dict:
    """Extract preview fields from HTML. Never raises on broken markup."""
    parser = _MetaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    meta = _meta_map(parser.metas)
    raw_title = "".join(parser.title_parts)
    if "<" in raw_title:  # unclosed <title> ate the rest of a broken document
        raw_title = raw_title.split("<", 1)[0]
    title = _clip(
        _first(meta, "og:title", "twitter:title") or raw_title,
        TITLE_MAX,
    )
    description = _clip(
        _first(meta, "og:description", "twitter:description", "description"),
        DESC_MAX,
    )
    site_name = _clip(_first(meta, "og:site_name", "twitter:site", "application-name"), SITE_MAX)
    if site_name.startswith("@"):
        site_name = site_name[1:]
    base = parser.base_href and _safe_abs(page_url, parser.base_href) or page_url
    image = None
    for key in ("og:image:secure_url", "og:image", "twitter:image:src", "twitter:image"):
        image = _safe_abs(base, meta.get(key, ""))
        if image:
            break
    oembed = _safe_abs(base, parser.oembed_json or "")
    source = "html"
    if meta.get("og:title") or meta.get("og:image") or meta.get("og:description"):
        source = "opengraph"
    elif meta.get("twitter:title") or meta.get("twitter:image") or meta.get("twitter:description"):
        source = "twitter"
    canonical = _safe_abs(base, _first(meta, "og:url")) or page_url
    return {
        "title": title,
        "description": description,
        "site_name": site_name,
        "image_url": image,
        "oembed_url": oembed,
        "canonical_url": canonical,
        "source": source,
    }


def parse_oembed(body: bytes, content_type: str = "") -> dict:
    """Return {title, site_name, image_url} from oEmbed JSON, or empty dict."""
    ct = (content_type or "").lower()
    if ct and "json" not in ct and "javascript" not in ct:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    title = _clip(str(data.get("title") or ""), TITLE_MAX)
    site = _clip(str(data.get("provider_name") or ""), SITE_MAX)
    thumb = data.get("thumbnail_url")
    image = thumb if isinstance(thumb, str) else None
    if image and image.lower().startswith(("javascript:", "data:", "file:")):
        image = None
    return {"title": title, "site_name": site, "image_url": image, "source": "oembed"}
