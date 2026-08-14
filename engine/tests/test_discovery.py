"""Ported from doc_to_md.py selftest(): sitemap parsing, including the index recursion."""

import gzip

from iofast_engine.discovery import MAX_BODY, _body_text, parse_llms, parse_sitemap


class FakeResponse:
    def __init__(self, body):
        self.body = body


def test_parse_sitemap_urlset():
    pages, nested = parse_sitemap(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://x/a</loc><lastmod>2026-01-01</lastmod></url></urlset>"
    )
    assert pages == [("https://x/a", "2026-01-01")] and nested == []


def test_parse_sitemap_index_recursion():
    _, nested = parse_sitemap(
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://x/s2.xml.gz</loc></sitemap></sitemapindex>"
    )
    assert nested == ["https://x/s2.xml.gz"]


def test_parse_sitemap_malformed_must_not_raise():
    assert parse_sitemap("not xml") == ([], []), "malformed sitemap must not raise"


def test_parse_llms():
    urls = parse_llms("- [Intro](/docs/intro)\nhttps://x.com/docs/api\n", "https://x.com/llms.txt")
    assert "https://x.com/docs/intro" in urls
    assert "https://x.com/docs/api" in urls


def test_body_text_gunzips():
    assert _body_text(FakeResponse(gzip.compress(b"<urlset/>"))) == "<urlset/>"


def test_body_text_caps_raw_body():
    assert _body_text(FakeResponse(b"x" * (MAX_BODY + 1))) == ""


def test_body_text_caps_decompressed_body():
    """A gzip bomb must not be expanded past the cap."""
    bomb = gzip.compress(b"\0" * (MAX_BODY + 1024))
    assert len(bomb) < MAX_BODY, "fixture must be small compressed, huge expanded"
    assert _body_text(FakeResponse(bomb)) == ""


def test_markdown_may_open_with_an_html_comment():
    """docsify.js.org/ui-kit.md is 17KB of real markdown behind a markdownlint pragma.

    Rejecting it on a bare "starts with <" filed the whole page as not-markdown
    (measured live 2026-08-14) — and the same pragma opens its `_sidebar.md`.
    """
    from iofast_engine.discovery import looks_like_markdown

    headers = {"content-type": "text/markdown; charset=utf-8"}
    body = "<!-- markdownlint-disable single-title -->\n\n# UI Kit\n\n" + "prose. " * 60
    assert looks_like_markdown(200, headers, body)
    assert not looks_like_markdown(200, headers, "<!-- saved from url -->\n<html>" + "x" * 400)
    assert not looks_like_markdown(200, headers, "<html><body>" + "x" * 400)
    assert not looks_like_markdown(200, headers, "<!-- unterminated comment " + "x" * 400)
