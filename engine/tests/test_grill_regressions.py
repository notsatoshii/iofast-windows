"""Round-2 /grill regression repros: 304-on-twin honored, robots gates the
crawl-fallback seed, flat /en/<page> sitemaps stay sitemap-mode, probe-class
update seeds keep md. Written by the adversarial reviewer, folded in as-is."""

import sys, asyncio
sys.path.insert(0, "tests")
from test_ledger_fixtures import FakeResponse, drain, URL, spider  # noqa

def test_304_on_md_twin_is_honored(spider):
    """Update run of a twin site: the twin answers 304 (unchanged). The md_probe
    branch must not throw that away and re-fetch the page as HTML."""
    spider.previous = {URL: {"file": "guide-setup.md", "chars": 4000, "etag": '"v1"'}}
    spider.resume = False
    spider.validators = {}
    spider.md_map = {URL: URL + ".md"}
    spider.ledger.enqueue(URL)
    resp = FakeResponse(URL + ".md", status=304, headers={"ETag": '"v1"'},
                        meta={"depth": 0, "origin_url": URL, "md_probe": True})
    yielded = drain(spider.parse(resp))
    print("YIELDED:", [(r.url, r.meta.get("md_probe")) for r in yielded])
    print("RECORD:", spider.ledger.records.get(URL))
    assert yielded == [], "304 on the twin was discarded; page re-fetched as HTML"
    assert spider.ledger.records[URL]["reason"] == "not modified (304)"

import asyncio
import iofast_engine.spider as spider_mod
from iofast_engine.spider import DocSpider

START = "https://docs.example.com/en/master/usage/quickstart.html"

def test_crawl_mode_start_url_respects_robots(tmp_path, monkeypatch):
    monkeypatch.setattr(spider_mod, "discover_sitemap", lambda o, e, emit=None: [])
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda o: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda o, s, emit=None: (None, []))
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: False)  # robots: Disallow /
    sp = DocSpider(START, out_dir=str(tmp_path), resolve=False, scope="/en/master")
    async def collect():
        return [r.url async for r in sp.start_requests()]
    urls = asyncio.run(collect())
    print("DISCOVERY:", sp.discovery, "REQUESTED:", urls)
    assert urls == [], "robots Disallow: / — nothing may be fetched"

import iofast_engine.spider as spider_mod
from iofast_engine.spider import DocSpider

def test_flat_locale_docs_site_keeps_sitemap(tmp_path, monkeypatch):
    """A flat /en/<page> docs site is content, not version roots."""
    pages = [(f"https://docs.example.com/en/{p}", None)
             for p in ("intro", "api", "guide", "faq")]
    monkeypatch.setattr(spider_mod, "discover_sitemap", lambda o, e, emit=None: pages)
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda o: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda o, s, emit=None: (None, []))
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: True)
    sp = DocSpider("https://docs.example.com/en/", out_dir=str(tmp_path), resolve=False, scope="/en/")
    print("DISCOVERY:", sp.discovery)
    assert sp.discovery == "sitemap"

def test_readme_probe_update_keeps_twins(tmp_path, monkeypatch):
    """ReadMe-shape probe run: md is ON but md_map is empty, so previous-manifest
    pages are re-fetched as HTML over their markdown files."""
    llms = ("https://docs.example.com/llms.txt",
            ["https://docs.example.com/en/master/a", "https://docs.example.com/en/master/b"])
    monkeypatch.setattr(spider_mod, "discover_sitemap", lambda o, e, emit=None: [])
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda o: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda o, s, emit=None: llms)
    monkeypatch.setattr(spider_mod, "probe_md_twin", lambda url: True)
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: True)
    prev = {"https://docs.example.com/en/master/old": {"file": "old.md", "chars": 4000}}
    sp = DocSpider("https://docs.example.com/en/master/usage/quickstart.html",
                   out_dir=str(tmp_path), resolve=False, scope="/en/master",
                   previous=prev)
    import asyncio
    async def collect():
        return [(r.url, r.meta.get("md_probe")) async for r in sp.start_requests()]
    reqs = asyncio.run(collect())
    print("MD_MAP:", sp.md_map, "SEEDS:", sp.seeds)
    print("REQS:", reqs)
    old = [m for u, m in reqs if u.startswith("https://docs.example.com/en/master/old")]
    assert old == [True], "previous twin page refetched as HTML on a probe-enabled site"


import asyncio

START = "https://docs.example.com/en/master/usage/quickstart.html"

def test_update_seeds_respect_new_robots(tmp_path, monkeypatch):
    """robots.txt now disallows everything; an update run must not refetch
    previously-written pages."""
    monkeypatch.setattr(spider_mod, "discover_sitemap", lambda o, e, emit=None: [])
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda o: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda o, s, emit=None: (None, []))
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: False)
    prev = {"https://docs.example.com/en/master/old": {"file": "old.md", "chars": 4000}}
    sp = DocSpider(START, out_dir=str(tmp_path), resolve=False, scope="/en/master", previous=prev)
    async def collect():
        return [r.url async for r in sp.start_requests()]
    urls = asyncio.run(collect())
    print("REQUESTED:", urls)
    assert urls == [], "robots Disallow: / — previous-manifest pages still fetched"

def test_probe_sample_skips_extension_urls(tmp_path, monkeypatch):
    """Sphinx-shape .html urls have no `<page>.html.md` twin; sampling one
    turns md off for a site whose extension-less pages are twinned."""
    probed = []
    llms = ("https://docs.example.com/llms.txt",
            ["https://docs.example.com/en/master/install.html",
             "https://docs.example.com/en/master/guide"])
    monkeypatch.setattr(spider_mod, "discover_sitemap", lambda o, e, emit=None: [])
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda o: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda o, s, emit=None: llms)
    monkeypatch.setattr(spider_mod, "probe_md_twin",
                        lambda url: probed.append(url) or url.endswith("guide.md"))
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: True)
    sp = DocSpider(START, out_dir=str(tmp_path), resolve=False, scope="/en/master")
    print("PROBED:", probed, "MD_ON:", sp.md_on)
    assert sp.md_on, "md turned off because the probe sampled a .html url"
