"""Re-encode preview images and strip EXIF so clients never hotlink origin hosts."""
from __future__ import annotations

import io

from .ssrf import SSRFError

MAX_SIDE = 1200
JPEG_QUALITY = 82
MAX_PIXELS = 40_000_000  # Pillow bomb cap (below default ~89MP)

# SVG / HTML-as-image are XSS vectors in some clients — refuse before Pillow.
_FORBIDDEN_PREFIXES = (
    b"<svg",
    b"<?xml",
    b"<!doctype html",
    b"<html",
)


def reencode_preview(data: bytes) -> bytes:
    """Return a JPEG with no EXIF / ICC-as-PII. Raises SSRFError on refusal."""
    if not data or len(data) < 24:
        raise SSRFError("blocked")
    head = data[:256].lstrip().lower()
    if any(head.startswith(p) for p in _FORBIDDEN_PREFIXES):
        raise SSRFError("blocked")
    try:
        from PIL import Image, ImageFile, ImageOps
    except ImportError as e:  # pragma: no cover
        raise SSRFError("blocked") from e
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = ImageOps.exif_transpose(im) or im
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        result = out.getvalue()
    except Exception as e:
        raise SSRFError("blocked") from e
    if not result.startswith(b"\xff\xd8"):
        raise SSRFError("blocked")
    return result


def make_stub_jpeg(title: str = "Talking") -> bytes:
    """Tiny generated JPEG used by UNFURL_STUB / tests (no network)."""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (640, 336), (20, 28, 48))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 280, 640, 336], fill=(42, 212, 198))
    draw.rectangle([0, 0, 640, 8], fill=(139, 92, 246))
    try:
        draw.text((24, 120), title[:48], fill=(232, 238, 252))
    except Exception:
        pass
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=80)
    return out.getvalue()
