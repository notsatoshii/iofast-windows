"""Doc-site shape gaps found by the 2026-08-13 11-site probe matrix.

Two discovery defects, both offline-tested by stubbing the fetch seams:

* RTD-style sitemap collapse: Read the Docs sitemaps list VERSION ROOTS only
  (measured: sphinx-doc.org sitemap.xml = exactly 1 <loc>). A non-empty sitemap
  used to lock discovery to "sitemap" mode (no link crawl) and start_requests
  dropped the user's start URL — the whole crawl collapsed to one landing page.

* ReadMe-style llms.txt: the index lists PLAIN page urls, yet `.md` twins exist
  (docs.readme.com serves both). `md_twins()` is rightly empty for plain links,
  so md stayed off and every page burned the browser tier instead of fetching
  the twin. A one-request probe of a real twin now proves the convention;
  a bundle-only llms.txt (svelte.dev) fails the probe and stays md-off.
"""

import asyncio

import iofast_engine.spider as spider_mod
from iofast_engine.discovery import probe_md_twin
from iofast_engine.spider import DocSpider

START = "https://docs.example.com/en/master/usage/quickstart.html"
VERSION_ROOT = "https://docs.example.com/en/master"
PAGE_A = "https://docs.example.com/en/master/a"
PAGE_B = "https://docs.example.com/en/master/b"


def _spider(tmp_path, monkeypatch, *, sitemap=(), llms=(None, []), probe=False, scope="/en/master"):
    monkeypatch.setattr(
        spider_mod, "discover_sitemap", lambda origin, extra, emit=None: list(sitemap)
    )
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda origin: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda origin, scope_root, emit=None: llms)
    monkeypatch.setattr(spider_mod, "probe_md_twin", lambda url: probe)
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: True)
    return DocSpider(START, out_dir=str(tmp_path), resolve=False, scope=scope)


def _seed_urls(spider):
    async def collect():
        return [r.url async for r in spider.start_requests()]

    return asyncio.run(collect())


def test_degenerate_sitemap_stays_in_crawl_mode(tmp_path, monkeypatch):
    """One version-root loc (the measured RTD shape) must not disable link crawl."""
    spider = _spider(tmp_path, monkeypatch, sitemap=[(VERSION_ROOT, None)])
    assert spider.discovery == "crawl"
    assert VERSION_ROOT in {url for url, _ in spider.seeds}


def test_version_roots_only_sitemap_stays_in_crawl_mode(tmp_path, monkeypatch):
    """urllib3/celery shape (measured 2026-08-13): 6+ version-root locs clear
    the yield floor but are still zero content pages — must stay crawl mode."""
    roots = [
        (f"https://docs.example.com/en/{v}", None)
        for v in ("latest", "stable", "2.7.0", "2.6.1", "2.5.0", "1.26.x")
    ]
    spider = _spider(tmp_path, monkeypatch, sitemap=roots, scope="/")
    assert spider.discovery == "crawl"
    assert len(spider.seeds) == 6


def test_rich_sitemap_keeps_sitemap_mode(tmp_path, monkeypatch):
    pages = [(f"{VERSION_ROOT}/p{i}", None) for i in range(6)]
    spider = _spider(tmp_path, monkeypatch, sitemap=pages)
    assert spider.discovery == "sitemap"


def test_start_url_always_seeded(tmp_path, monkeypatch):
    """The user's start URL is a promise — a sitemap that omits it must not drop it."""
    spider = _spider(tmp_path, monkeypatch, sitemap=[(VERSION_ROOT, None)])
    assert _seed_urls(spider)[0] == spider.start_url


def test_start_url_moved_to_frontier_front(tmp_path, monkeypatch):
    """Present-but-buried start URL must lead, or max_pages truncation cuts it
    (observed live: vitepress 18-url frontier, --max-pages 3, seed page dropped)."""
    pages = [(f"{VERSION_ROOT}/p{i}", None) for i in range(6)] + [
        ("https://docs.example.com/en/master/usage/quickstart.html", None)
    ]
    spider = _spider(tmp_path, monkeypatch, sitemap=pages)
    assert _seed_urls(spider)[0] == spider.start_url


def test_plain_llms_links_with_proven_twin_enable_md(tmp_path, monkeypatch):
    llms = ("https://docs.example.com/llms.txt", [PAGE_A, PAGE_B])
    spider = _spider(tmp_path, monkeypatch, llms=llms, probe=True)
    assert spider.seeds and all(md for _, md in spider.seeds)
    # md_probe seeds fetch the twin url, not the page url
    assert _seed_urls(spider)[-1].endswith(".md")


def test_index_convention_twins_fetch_exact_llms_url(tmp_path, monkeypatch):
    """Cloudflare-style `<path>/index.md` twins: the seed request must target the
    exact llms.txt url, not a re-derived `<path>.md` (which 404s there)."""
    twin = "https://docs.example.com/en/master/get-started/index.md"
    llms = ("https://docs.example.com/llms.txt", [twin])
    spider = _spider(tmp_path, monkeypatch, llms=llms)
    targets = _seed_urls(spider)
    assert twin in targets
    assert "https://docs.example.com/en/master/get-started.md" not in targets


def test_plain_llms_links_with_failed_probe_stay_md_off(tmp_path, monkeypatch):
    """Bundle-only llms.txt (svelte class): probe 404s, behavior unchanged from #29."""
    llms = ("https://docs.example.com/llms.txt", [PAGE_A, PAGE_B])
    spider = _spider(tmp_path, monkeypatch, llms=llms, probe=False)
    assert spider.seeds and not any(md for _, md in spider.seeds)


def test_nested_llms_index_expands_to_page_links(monkeypatch):
    """ReadMe's root llms.txt is an index OF llms.txt files; the leaves hold the
    real `.md` page links (measured docs.readme.com 2026-08-13). One level of
    expansion must surface them; a bundle-only or flat llms.txt is untouched."""
    import iofast_engine.discovery as disc

    root = "https://docs.example.com/llms.txt"
    section = "https://docs.example.com/main/docs/start/llms.txt"
    bodies = {
        root: f"- [Main](https://docs.example.com/main/llms.txt)\n- [Start]({section})\n",
        "https://docs.example.com/main/llms.txt": "- [Deep](https://docs.example.com/main/docs/deep/llms.txt)\n",
        section: "- [Welcome](https://docs.example.com/main/docs/welcome.md)\n"
        "- [Quickstart](https://docs.example.com/main/docs/quickstart.md)\n",
    }

    def fake_get(url, timeout=20, headers=None):
        body = bodies.get(url)
        return _Resp(200, "text/plain", body.encode()) if body else _Resp(404, "text/html", b"")

    monkeypatch.setattr(disc, "_get", fake_get)
    llms_url, found = disc.discover_llms("https://docs.example.com", "https://docs.example.com")
    assert llms_url == root
    assert "https://docs.example.com/main/docs/welcome.md" in found
    assert "https://docs.example.com/main/docs/quickstart.md" in found
    # llms.txt index urls themselves must NOT leak into the page list
    assert not any(u.endswith("/llms.txt") for u in found)


def test_nested_bundle_llms_stays_unexpanded(monkeypatch):
    """Svelte shape: nested llms.txt files are CONTENT BUNDLES whose embedded
    plain links are content refs — expanding them seeded 404 pages (measured
    svelte.dev 2026-08-13). No `.md` links -> original list returned untouched."""
    import iofast_engine.discovery as disc

    root = "https://svelte.example/llms.txt"
    bundle = "https://svelte.example/docs/svelte/llms.txt"
    # Real bundles are mostly prose but DO contain scattered bullet links
    # (measured svelte docs/svelte/llms.txt: 33 bullet lines / 12261) — the
    # ratio floor, not the bullet regex, is what must exclude them.
    bundle_body = "prose line about svelte\n" * 40 + (
        "- [content ref](https://svelte.example/docs/ai/attach)\n"
        "- [external](https://github.com/x/y/blob/main/docs/config.md)\n"
    )
    bodies = {
        root: f"- [Svelte docs]({bundle})\n",
        bundle: bundle_body,
    }

    def fake_get(url, timeout=20, headers=None):
        body = bodies.get(url)
        return _Resp(200, "text/plain", body.encode()) if body else _Resp(404, "text/html", b"")

    monkeypatch.setattr(disc, "_get", fake_get)
    llms_url, found = disc.discover_llms("https://svelte.example", "https://svelte.example")
    assert llms_url == root
    assert found == [bundle]  # untouched: no proven .md twins anywhere
    assert "https://svelte.example/docs/ai/attach" not in found


def test_probe_skips_urls_without_a_real_twin(tmp_path, monkeypatch):
    """md_variant leaves the site root unchanged — probing it would fetch HTML
    and wrongly keep md off for the run. The sample must skip such urls."""
    probed = []

    def fake_probe(url):
        probed.append(url)
        return True

    root = "https://docs.example.com/"  # md_variant(root) == root: no twin
    llms = ("https://docs.example.com/llms.txt", [root, PAGE_A])
    monkeypatch.setattr(
        spider_mod, "discover_sitemap", lambda origin, extra, emit=None: []
    )
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda origin: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda origin, scope_root, emit=None: llms)
    monkeypatch.setattr(spider_mod, "probe_md_twin", fake_probe)
    monkeypatch.setattr(spider_mod.Robots, "allowed", lambda self, url: True)
    DocSpider(START, out_dir=str(tmp_path), resolve=False, scope="/")
    assert probed == [PAGE_A + ".md"]


def test_flat_md_list_skips_nested_bundle_fetch(monkeypatch):
    """A flat Mintlify list with direct `.md` links plus a bulleted llms-full.txt
    must not download the bundle just to reject it by ratio."""
    import iofast_engine.discovery as disc

    root = "https://mint.example/llms.txt"
    fetched = []

    def fake_get(url, timeout=20, headers=None):
        fetched.append(url)
        if url == root:
            body = (
                "- [A](https://mint.example/docs/a.md)\n"
                "- [Full](https://mint.example/llms-full.txt)\n"
            )
            return _Resp(200, "text/plain", body.encode())
        return _Resp(404, "text/html", b"")

    monkeypatch.setattr(disc, "_get", fake_get)
    _, found = disc.discover_llms("https://mint.example", "https://mint.example")
    assert "https://mint.example/docs/a.md" in found
    assert "https://mint.example/llms-full.txt" not in fetched


class _Resp:
    def __init__(self, status, ctype, body):
        self.status = status
        self.headers = {"Content-Type": ctype}
        self.body = body


def test_numbered_list_index_expands(monkeypatch):
    """Numbered-list llms.txt indexes (`1. [x](url)`) count as indexes too."""
    import iofast_engine.discovery as disc

    root = "https://n.example/llms.txt"
    section = "https://n.example/docs/llms.txt"
    bodies = {
        root: f"- [Section]({section})\n",
        section: "1. [A](https://n.example/docs/a.md)\n2. [B](https://n.example/docs/b.md)\n",
    }

    def fake_get(url, timeout=20, headers=None):
        body = bodies.get(url)
        return _Resp(200, "text/plain", body.encode()) if body else _Resp(404, "text/html", b"")

    monkeypatch.setattr(disc, "_get", fake_get)
    _, found = disc.discover_llms("https://n.example", "https://n.example")
    assert "https://n.example/docs/a.md" in found


def test_robots_disallowed_start_url_not_force_seeded(tmp_path, monkeypatch):
    """The always-seed-start-url guarantee stays subject to robots.txt —
    forcing it in unconditionally would bypass the obey policy in the receipt."""
    pages = [(f"{VERSION_ROOT}/p{i}", None) for i in range(6)]
    monkeypatch.setattr(
        spider_mod, "discover_sitemap", lambda origin, extra, emit=None: pages
    )
    monkeypatch.setattr(spider_mod, "robots_sitemaps", lambda origin: [])
    monkeypatch.setattr(spider_mod, "discover_llms", lambda origin, scope_root, emit=None: (None, []))
    monkeypatch.setattr(
        spider_mod.Robots,
        "allowed",
        lambda self, url: not url.rstrip("/").endswith("quickstart.html"),
    )
    spider = DocSpider(START, out_dir=str(tmp_path), resolve=False, scope="/en/master")
    assert spider.start_url not in _seed_urls(spider)


def test_two_letter_dirs_not_misread_as_version_roots(tmp_path, monkeypatch):
    """/go/install, /js/install are content pages — only REAL locale codes
    (urls.LOCALE_RE) mark a two-segment path as a version root."""
    pages = [
        (f"https://docs.example.com/{d}/install", None) for d in ("go", "js", "py", "ui")
    ]
    spider = _spider(tmp_path, monkeypatch, sitemap=pages, scope="/")
    assert spider.discovery == "sitemap"


def test_nested_llms_never_fetches_offsite_hosts(monkeypatch):
    """llms.txt is untrusted input — nested index links must stay on-host."""
    import iofast_engine.discovery as disc

    fetched = []
    root = "https://docs.example.com/llms.txt"
    bodies = {
        root: b"- [Partner](https://evil.example/llms.txt)\n"
        b"- [Internal](http://169.254.169.254/latest/llms.txt)\n",
        "https://evil.example/llms.txt": b"- [A](https://evil.example/a.md)\n",
    }

    def fake_get(url, timeout=20, headers=None):
        fetched.append(url)
        body = bodies.get(url)
        return _Resp(200, "text/plain", body) if body else _Resp(404, "text/html", b"")

    monkeypatch.setattr(disc, "_get", fake_get)
    disc.discover_llms("https://docs.example.com", "https://docs.example.com")
    assert "https://evil.example/llms.txt" not in fetched
    assert "http://169.254.169.254/latest/llms.txt" not in fetched


def test_probe_md_twin_accept_and_reject(monkeypatch):
    import iofast_engine.discovery as disc

    md_body = ("# Title\n\n" + "prose " * 100).encode()
    cases = [
        (_Resp(200, "text/markdown; charset=utf-8", md_body), True),
        (_Resp(200, "text/plain", md_body), True),
        (_Resp(200, "text/html", b"<!doctype html>" + md_body), False),  # soft-404 shell
        (_Resp(404, "text/markdown", md_body), False),
        (_Resp(200, "text/markdown", b"tiny"), False),  # under THIN_CHARS = soft-404
        (None, False),  # network failure
    ]
    for resp, expected in cases:
        monkeypatch.setattr(disc, "_get", lambda url, r=resp: r)
        assert probe_md_twin("https://x/a.md") is expected, (resp, expected)
