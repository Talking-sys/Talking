"""HTML metadata parser: Open Graph, Twitter Card, title, broken markup, encodings."""
from talking.unfurl.parser import decode_html, parse_html, parse_oembed
from talking.unfurl.urls import extract_urls

OG_HTML = """<!doctype html>
<html><head>
<title>Ignore me</title>
<meta property="og:site_name" content="News Site">
<meta property="og:title" content="OG Title">
<meta property="og:description" content="OG description about the story.">
<meta property="og:image" content="https://cdn.news.example/preview.jpg">
<meta property="og:url" content="https://news.example/story">
</head><body></body></html>"""

TWITTER_HTML = """<!doctype html>
<html><head>
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@news">
<meta name="twitter:title" content="Tweet Title">
<meta name="twitter:description" content="From a tweet card.">
<meta name="twitter:image" content="https://cdn.news.example/tw.jpg">
<title>Fallback title</title>
</head></html>"""

PLAIN_HTML = """<!doctype html><html><head>
<title>  Plain   Title  </title>
<meta name="description" content="A plain description.">
</head><body><h1>Hi</h1></html>"""

BROKEN_HTML = """<html><title>Broken OG</title>
<meta property="og:description" content="still parsed"
<img src="x"><p>no close"""

JS_IMAGE_HTML = """<html><head>
<meta property="og:title" content="Evil">
<meta property="og:image" content="javascript:alert(1)">
</head></html>"""


def test_open_graph():
    d = parse_html(OG_HTML, "https://news.example/story")
    assert d["source"] == "opengraph"
    assert d["title"] == "OG Title"
    assert d["description"].startswith("OG description")
    assert d["site_name"] == "News Site"
    assert d["image_url"] == "https://cdn.news.example/preview.jpg"
    assert d["canonical_url"] == "https://news.example/story"


def test_twitter_card():
    d = parse_html(TWITTER_HTML, "https://news.example/t")
    assert d["source"] == "twitter"
    assert d["title"] == "Tweet Title"
    assert d["site_name"] == "news"
    assert d["image_url"].endswith("/tw.jpg")


def test_plain_title_and_description():
    d = parse_html(PLAIN_HTML, "https://example.com/x")
    assert d["source"] == "html"
    assert d["title"] == "Plain Title"
    assert d["description"] == "A plain description."


def test_broken_html_still_yields_fields():
    d = parse_html(BROKEN_HTML, "https://example.com/b")
    assert d["title"] == "Broken OG"
    assert d["description"] == "still parsed"


def test_javascript_image_rejected():
    d = parse_html(JS_IMAGE_HTML, "https://example.com/e")
    assert d["image_url"] is None
    assert d["title"] == "Evil"


def test_relative_image_resolved():
    html = '<meta property="og:image" content="/img/a.jpg"><title>T</title>'
    d = parse_html(html, "https://example.com/dir/page")
    assert d["image_url"] == "https://example.com/img/a.jpg"


def test_non_utf8_windows1251():
    title = "Привет мир"
    raw = (
        b'<!doctype html><html><head><meta charset="windows-1251"><title>'
        + title.encode("windows-1251")
        + b"</title></head></html>"
    )
    html = decode_html(raw, "text/html")
    d = parse_html(html, "https://example.com/ru")
    assert "Привет" in d["title"]


def test_decode_respects_content_type_header():
    raw = "Café".encode("latin-1")
    html = decode_html(
        b"<title>" + raw + b"</title>",
        "text/html; charset=latin-1",
    )
    assert "Caf" in html


def test_oembed_json():
    body = b'{"title": "Video", "provider_name": "Tube", "thumbnail_url": "https://t.example/a.jpg"}'
    d = parse_oembed(body, "application/json")
    assert d["title"] == "Video"
    assert d["site_name"] == "Tube"
    assert d["image_url"].endswith("/a.jpg")


def test_oembed_rejects_data_thumbnail():
    body = b'{"title": "x", "thumbnail_url": "data:image/png;base64,aaa"}'
    d = parse_oembed(body, "application/json")
    assert d["image_url"] is None


def test_extract_urls_strips_punctuation():
    text = "see https://example.com/a, and https://example.com/b)."
    urls = extract_urls(text)
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_extract_urls_unique_and_scheme_required():
    assert extract_urls("go to example.com please") == []
    assert extract_urls("https://a.example https://a.example") == ["https://a.example"]
