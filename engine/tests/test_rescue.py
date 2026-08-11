"""Tier 3 LLM rescue: env gating, caching, selector validation. All HTTP mocked."""

import json

import pytest

import iofast_engine.rescue as rescue


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("IOFAST_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("IOFAST_RESCUE_MODEL", raising=False)


def arm(monkeypatch, reply):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("IOFAST_RESCUE_MODEL", "test/model")
    calls = []

    def fake_call(api_key, model, url, html, timeout=60):
        calls.append({"api_key": api_key, "model": model, "url": url})
        return reply

    monkeypatch.setattr(rescue, "_call_llm", fake_call)
    return calls


def test_unarmed_returns_none_with_warning():
    events = []
    assert rescue.rescue_selectors("https://x.com/docs/a", "<html/>", emit=events.append) is None
    assert events[0]["type"] == "warning"
    assert "OPENROUTER_API_KEY" in events[0]["message"]


def test_rescue_caches_per_domain(monkeypatch):
    calls = arm(monkeypatch, '{"content_selector": "div.doc-body", "nav_selector": "nav.toc"}')
    events = []
    first = rescue.rescue_selectors("https://x.com/docs/a", "<html/>", emit=events.append)
    assert first["content_selector"] == "div.doc-body"
    assert first["nav_selector"] == "nav.toc"
    assert len(calls) == 1
    # Second page on the same domain: cache hit, no second call.
    second = rescue.rescue_selectors("https://x.com/docs/b", "<html/>", emit=events.append)
    assert second["content_selector"] == "div.doc-body"
    assert len(calls) == 1, "one call per DOMAIN is the contract"
    assert any(e.get("cached") for e in events if e["type"] == "rescue")
    # Cache file on disk holds the candidate adapter entry.
    with open(rescue.cache_path(), encoding="utf-8") as fh:
        cache = json.load(fh)
    assert cache["x.com"]["model"] == "test/model"


def test_code_fenced_reply_is_parsed(monkeypatch):
    arm(monkeypatch, '```json\n{"content_selector": "main.docs", "nav_selector": null}\n```')
    got = rescue.rescue_selectors("https://y.com/a", "<html/>", emit=None)
    assert got["content_selector"] == "main.docs"
    assert got["nav_selector"] is None


def test_unusable_selector_is_rejected_not_cached(monkeypatch):
    """MUST-FAIL input: a selector that does not compile never enters the cache."""
    arm(monkeypatch, '{"content_selector": ":::garbage(((", "nav_selector": null}')
    events = []
    assert rescue.rescue_selectors("https://z.com/a", "<html/>", emit=events.append) is None
    assert any("unusable selector" in e["message"] for e in events)
    with pytest.raises(FileNotFoundError):
        open(rescue.cache_path(), encoding="utf-8")


def test_call_failure_returns_none_uncached(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("IOFAST_RESCUE_MODEL", "test/model")

    def boom(*args, **kwargs):
        raise TimeoutError("upstream timed out")

    monkeypatch.setattr(rescue, "_call_llm", boom)
    events = []
    assert rescue.rescue_selectors("https://w.com/a", "<html/>", emit=events.append) is None
    assert any("rescue call failed" in e["message"] for e in events)


def test_key_never_in_events(monkeypatch):
    arm(monkeypatch, '{"content_selector": "main", "nav_selector": null}')
    events = []
    rescue.rescue_selectors("https://q.com/a", "<html/>", emit=events.append)
    assert "sk-test" not in json.dumps(events), "the API key must never leak into events"


def _stub_spider(tmp_path):
    """Minimal DocSpider state for driving parse() offline (mirrors test_ledger_fixtures)."""
    import iofast_engine.spider as spider_mod
    from iofast_engine.ledger import Ledger
    from iofast_engine.spider import DocSpider
    from iofast_engine.writer import Writer

    s = DocSpider.__new__(DocSpider)
    s.emit = lambda event: None
    s.out_dir = str(tmp_path)
    s.ledger = Ledger()
    s.writer = Writer(s.out_dir, s.ledger, max_pages=10, emit=s.emit)
    s.browsers = False
    s.best, s.attempts, s.started, s.seen = {}, {}, {}, set()
    s.manifest = {}
    s.adapter = None
    s.rescue_attempted = False
    s.rescued = None
    s.last_status = {}
    s.robots = type("Robots", (), {"allowed": staticmethod(lambda url: True)})()
    s.selectors = spider_mod.CONTENT_SELECTORS
    s.start_tier = "static"
    s.max_pages, s.max_depth = 10, 3
    s.discovery = "sitemap"
    s.scope, s.host = "/docs/", "x.com"
    s.keep_locales = frozenset()
    s._engine = object()
    return s


def test_spider_adopts_rescued_selectors(tmp_path, monkeypatch):
    """Thin page + no adapter -> one rescue -> selectors adopted -> page written."""
    import asyncio

    import iofast_engine.spider as spider_mod

    s = _stub_spider(tmp_path)
    monkeypatch.setattr(
        spider_mod,
        "rescue_selectors",
        lambda url, html, emit=None: {"content_selector": "div.payload", "nav_selector": None},
    )
    monkeypatch.setattr(spider_mod, "detect_late", lambda *a, **k: None)

    def fake_page_content(html, url, selectors):
        if selectors[0] == "div.payload":
            return "X" * 300, "selector+markdownify"
        return "", "none"

    monkeypatch.setattr(spider_mod, "page_content", fake_page_content)

    class FakeResponse:
        url = "https://x.com/docs/a"
        status = 200
        body = b"<html><div class='payload'>hi</div></html>"
        headers = {}
        meta = {"depth": 0, "origin_url": "https://x.com/docs/a"}

    s.ledger.enqueue(FakeResponse.url)

    async def run():
        return [item async for item in s.parse(FakeResponse())]

    assert asyncio.run(run()) == []
    record = s.ledger.records[FakeResponse.url]
    assert record["outcome"] == "ok"
    assert record["reason"].startswith("rescue:")
    assert s.rescue_attempted and s.rescued["content_selector"] == "div.payload"
    assert s.selectors[0] == "div.payload"
