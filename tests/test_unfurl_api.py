"""API tests for unfurl + messages, with mocked outbound HTTP (no network)."""
import socket

from talking.unfurl.fetch import RawResponse, SafeFetcher
from talking.unfurl.images import make_stub_jpeg
from talking.unfurl.service import UnfurlService
from talking.unfurl.ssrf import SSRFError

from tests.test_unfurl_parser import OG_HTML, PLAIN_HTML

PUBLIC_IP = "93.184.216.34"


def public_gai(host, port, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 80))]


def service_from_pages(pages: dict[str, RawResponse]) -> UnfurlService:
    def hop(**kw):
        url = kw["url"]
        if url not in pages:
            raise SSRFError("blocked")
        return pages[url]

    fetcher = SafeFetcher(getaddrinfo=public_gai, hop=hop)
    return UnfurlService(fetcher=fetcher, stub=False)


def test_get_unfurl_opengraph(make_client):
    jpeg = make_stub_jpeg("img")
    pages = {
        "https://news.example/story": RawResponse(
            200, {"content-type": "text/html; charset=utf-8"}, OG_HTML.encode(), "https://news.example/story"
        ),
        "https://cdn.news.example/preview.jpg": RawResponse(
            200, {"content-type": "image/jpeg"}, jpeg, "https://cdn.news.example/preview.jpg"
        ),
    }
    svc = service_from_pages(pages)
    c = make_client(unfurl=svc)
    r = c.get("/api/unfurl", params={"url": "https://news.example/story"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "OG Title"
    assert body["site_name"] == "News Site"
    assert body["fallback"] is False
    assert body["image_path"].startswith("/api/unfurl/image/")
    img = c.get(body["image_path"])
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    assert img.content.startswith(b"\xff\xd8")
    assert "Talking servers fetch" in body["privacy"]


def test_unfurl_fallback_when_no_metadata(make_client):
    pages = {
        "https://example.com/empty": RawResponse(
            200, {"content-type": "text/html"}, b"<html><body>no tags</body></html>", "https://example.com/empty"
        ),
    }
    c = make_client(unfurl=service_from_pages(pages))
    body = c.get("/api/unfurl", params={"url": "https://example.com/empty"}).json()
    assert body["fallback"] is True
    assert body["site_name"] == "example.com"
    assert body["image_id"] is None


def test_unfurl_plain_title(make_client):
    pages = {
        "https://example.com/p": RawResponse(
            200, {"content-type": "text/html"}, PLAIN_HTML.encode(), "https://example.com/p"
        ),
    }
    body = make_client(unfurl=service_from_pages(pages)).get(
        "/api/unfurl", params={"url": "https://example.com/p"}
    ).json()
    assert body["title"] == "Plain Title"
    assert body["fallback"] is False


def test_unfurl_blocks_private(make_client):
    c = make_client()
    assert c.get("/api/unfurl", params={"url": "http://127.0.0.1/"}).status_code == 400
    assert c.get("/api/unfurl", params={"url": "file:///etc/passwd"}).status_code == 400


def test_post_message_attaches_preview(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    r = c.post("/api/chat/messages", json={"text": "look https://example.com/story"})
    assert r.status_code == 200
    msg = r.json()["message"]
    assert msg["kind"] == "text"
    assert msg["urls"] == ["https://example.com/story"]
    assert msg["preview"]["title"]
    assert msg["preview"]["image_path"] == "/api/unfurl/image/stub"
    listed = c.get("/api/chat/messages").json()["messages"]
    assert listed[-1]["id"] == msg["id"]


def test_post_message_rejects_too_long(make_client):
    c = make_client(env={"UNFURL_STUB": "1"})
    r = c.post("/api/chat/messages", json={"text": "x" * 501})
    assert r.status_code == 400


def test_chat_placeholder_still_501(make_client):
    """GET /api/chat is the live notes slice; partial still lists preview + edit."""
    c = make_client()
    r = c.get("/api/chat")
    assert r.status_code == 200
    partial = r.json()["partial"]
    assert "link_previews" in partial
    assert "message_edit" in partial
    assert "notes" in partial


def test_privacy_mentions_unfurl(make_client):
    html = make_client().get("/privacy").text
    assert "Open Graph" in html
    assert "hotlink" in html


def test_csp_allows_sandboxed_frames(make_client):
    c = make_client()
    csp = c.get("/").headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "frame-src 'self' https: http:" in csp
    assert c.get("/").headers["x-frame-options"] == "DENY"
    fix = c.get("/static/fixtures/sample-article.html")
    assert fix.status_code == 200
    assert fix.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'none'" not in fix.headers.get("content-security-policy", "")
    assert "link-preview.js" in c.get("/").text
    assert "miniBrowser" in c.get("/").text
