"""Map pass: section tree, nav-link parsing, and the discovery-only runner."""

import json
from pathlib import Path

from iofast_engine.ledger import Ledger
from iofast_engine.manifest import build_manifest
from iofast_engine.mapping import map_run, nav_links, render_tree, section_tree

FIXTURES = Path(__file__).parent / "fixtures" / "platforms"

URLS = [
    "https://x.com/docs",
    "https://x.com/docs/intro",
    "https://x.com/docs/api/auth",
    "https://x.com/docs/api/tokens",
    "https://x.com/docs/api/v2/refresh",
    "https://x.com/docs/guides/setup",
    "https://x.com/blog/post",  # out of scope — must not count
]


def test_section_tree_counts():
    tree = section_tree(URLS, "/docs/")
    assert tree["total"] == 6
    assert tree["pages"] == 2  # /docs itself (scope root) + /docs/intro
    assert tree["children"]["api"]["total"] == 3
    assert tree["children"]["api"]["pages"] == 2
    assert tree["children"]["api"]["children"]["v2"]["pages"] == 1
    assert tree["children"]["guides"]["total"] == 1


def test_render_tree_shows_counts():
    text = render_tree(section_tree(URLS, "/docs/"), "/docs/")
    assert "/docs/  (6 pages)" in text
    assert "api/  (3)" in text
    assert "v2/  (1)" in text
    assert "blog" not in text


def test_nav_links_from_readthedocs_fixture():
    html = (FIXTURES / "readthedocs" / "static-landing.html").read_text(encoding="utf-8")
    links = nav_links(html, "https://docs.readthedocs.io/en/stable/", "nav.wy-nav-side")
    assert links, "the Sphinx sidebar must yield links"
    assert all(link.startswith("http") for link in links)
    assert len(links) == len(set(links)), "deduped"


def test_nav_links_generic_fallback_and_bad_html():
    html = '<nav><a href="/docs/a">A</a><a href="mailto:x@y.z">bad</a></nav>'
    assert nav_links(html, "https://x.com/") == ["https://x.com/docs/a"]
    assert nav_links("", "https://x.com/") == []


def test_map_run_prefers_sitemap(monkeypatch):
    import iofast_engine.discovery as discovery

    monkeypatch.setattr(
        discovery, "discover_sitemap", lambda origin, extra, emit=None: [
            ("https://x.com/docs/a", None), ("https://x.com/docs/b/c", None)
        ]
    )
    monkeypatch.setattr(discovery, "robots_sitemaps", lambda origin: [])
    result = map_run("https://x.com/docs/", None)
    assert result["discovery"] == "sitemap"
    assert result["tree"]["total"] == 2
    assert result["scope"] == "/docs/"
    json.dumps(result)  # must be JSON-serializable for --json


def test_map_run_falls_back_to_nav(monkeypatch):
    import iofast_engine.discovery as discovery

    monkeypatch.setattr(discovery, "discover_sitemap", lambda origin, extra, emit=None: [])
    monkeypatch.setattr(discovery, "robots_sitemaps", lambda origin: [])
    monkeypatch.setattr(discovery, "discover_llms", lambda origin, root: (None, []))

    class FakeResponse:
        status = 200
        body = b'<nav><a href="/docs/x">X</a></nav>'
        headers = {}

    monkeypatch.setattr(discovery, "_get", lambda url, timeout=20: FakeResponse())
    result = map_run("https://x.com/docs/", None)
    assert result["discovery"] == "nav"
    assert result["pages"] == ["https://x.com/docs/x"]


def test_manifest_token_estimate():
    ledger = Ledger()
    for i, chars in enumerate((400, 600)):
        ledger.enqueue(f"u/{i}")
        ledger.record(f"u/{i}", "ok", chars=chars, file=f"{i}.md")
    ledger.enqueue("u/fail")
    ledger.record("u/fail", "failed_fetch", "http 403")  # no file — excluded from corpus
    manifest = build_manifest({}, ledger, True)
    assert manifest["token_estimate"] == 250
