"""In-memory and Redis-shaped cache, plus unfurl/message rate limits."""
import io
import time

from talking.security import TokenBucket
from talking.unfurl.cache import PreviewCache
from talking.unfurl.images import reencode_preview
from talking.unfurl.service import UnfurlService


class FakeRedis:
    def __init__(self):
        self.store = {}

    def ping(self):
        return True

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        assert ttl >= 1
        self.store[k] = v


def test_memory_cache_roundtrip_and_ttl():
    c = PreviewCache("")
    c.set_json("a", {"title": "t"}, ttl=1)
    assert c.get_json("a")["title"] == "t"
    c.set_bytes("b", b"jpeg", ttl=1)
    assert c.get_bytes("b") == b"jpeg"
    # expire
    with c._lock:
        k = c._k("a")
        exp, val = c._mem[k]
        c._mem[k] = (time.time() - 1, val)
    assert c.get_json("a") is None


def test_redis_backend_via_fake():
    c = PreviewCache("")
    c._redis = FakeRedis()
    c.backend = "redis"
    c.set_json("p", {"ok": True}, ttl=10)
    assert c.get_json("p") == {"ok": True}
    c.set_bytes("img", b"\xff\xd8", ttl=10)
    assert c.get_bytes("img") == b"\xff\xd8"


def test_service_caches_preview(monkeypatch):
    svc = UnfurlService(stub=True)
    a = svc.preview("https://example.com/story")
    b = svc.preview("https://example.com/story")
    assert a["title"] == b["title"]
    assert a["image_id"] == "stub"
    assert svc.image_bytes("stub").startswith(b"\xff\xd8")


def test_reencode_strips_exif():
    from PIL import Image

    im = Image.new("RGB", (64, 40), (200, 10, 10))
    exif = im.getexif()
    exif[0x010F] = "SecretCameraMake"
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    raw = buf.getvalue()
    assert b"SecretCameraMake" in raw
    out = reencode_preview(raw)
    assert out.startswith(b"\xff\xd8")
    assert b"SecretCameraMake" not in out
    im2 = Image.open(io.BytesIO(out))
    assert im2.getexif() is None or dict(im2.getexif()) == {}


def test_token_bucket_rate_limit():
    b = TokenBucket(60, burst=2)
    assert b.allow("k")
    assert b.allow("k")
    assert not b.allow("k")


def test_unfurl_http_rate_limit(make_client):
    c = make_client(env={"UNFURL_STUB": "1", "UNFURL_RATE_PER_MIN": "2"})
    url = "https://example.com/a"
    assert c.get("/api/unfurl", params={"url": url}).status_code == 200
    assert c.get("/api/unfurl", params={"url": url}).status_code == 200
    r = c.get("/api/unfurl", params={"url": url})
    assert r.status_code == 429


def test_chat_message_rate_limit(make_client):
    c = make_client(env={"UNFURL_STUB": "1", "CHAT_MSG_RATE_PER_MIN": "2"})
    body = {"text": "hello https://example.com/x"}
    assert c.post("/api/chat/messages", json=body).status_code == 200
    assert c.post("/api/chat/messages", json=body).status_code == 200
    assert c.post("/api/chat/messages", json=body).status_code == 429
