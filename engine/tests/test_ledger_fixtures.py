"""Ledger classification driven through DocSpider.parse/on_error, fully offline.

No network, no browser: fake request/response ducks carrying committed fixture
bytes, `browsers_available` forced False so the escalation ladder is off. Every
case asserts BOTH the ledger record and the rendered report.md, because a
classification that never reaches the report is a silent failure by another name.
"""

import asyncio
import json
from pathlib import Path

import pytest

from iofast_engine import spider as spider_mod
from iofast_engine.ledger import Ledger
from iofast_engine.spider import DocSpider
from iofast_engine.writer import LEDGER_FILE, Writer

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://docs.example.test/guide/setup"


class FakeResponse:
    """The attribute surface DocSpider.parse actually touches."""

    def __init__(self, url, status=200, body=b"", headers=None, meta=None):
        self.url = url
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.meta = meta or {"depth": 0, "origin_url": url}

    def css(self, _selector):  # only reached on discovery == "crawl"
        return []

    def urljoin(self, href):
        return href


class FakeRequest:
    """The attribute surface DocSpider.on_error actually touches."""

    def __init__(self, url, sid="static", meta=None):
        self.url = url
        self.sid = sid
        self.meta = meta or {"origin_url": url}


def drain(agen):
    """Run an async generator to exhaustion and return what it yielded."""

    async def run():
        return [item async for item in agen]

    return asyncio.run(run())


@pytest.fixture
def spider(tmp_path, monkeypatch):
    """A DocSpider with the run state parse/on_error/on_close need — and no __init__.

    __init__ resolves the start URL, reads robots.txt and hunts for a sitemap, all
    over the network. The classification logic under test needs none of it.
    """
    monkeypatch.setattr(spider_mod, "browsers_available", lambda: False)

    s = DocSpider.__new__(DocSpider)
    s.emit = lambda event: None
    s.out_dir = str(tmp_path)
    s.ledger = Ledger()
    s.writer = Writer(s.out_dir, s.ledger, max_pages=10, emit=s.emit)
    s.browsers = spider_mod.browsers_available()
    s.best = {}
    s.attempts = {}
    s.started = {}
    s.seen = set()
    s.manifest = {}
    s.adapter = None  # no platform detected: parse() re-detects on each 200
    s.rescue_attempted = True  # Tier 3 rescue off: these tests classify, not rescue
    s.rescued = None
    s.last_status = {}
    s.selectors = spider_mod.CONTENT_SELECTORS
    s.start_tier = "static"
    s.max_pages = 10
    s.max_depth = 3
    s.discovery = "sitemap"
    s.seeds = [(URL, False)]
    s.start_url = URL
    s.origin = "https://docs.example.test"
    s.host = "docs.example.test"
    s.scope = "/guide/"
    s.keep_locales = frozenset()
    s._engine = object()  # truthy: proves the retry branch is skipped by exhaustion, not absence
    return s


def report(spider):
    """Close the run the way a real crawl does, and read back the rendered report."""
    asyncio.run(spider.on_close())
    return (Path(spider.out_dir) / LEDGER_FILE).read_text(encoding="utf-8")


def failures_table(text):
    """Just the rows under '## Failures'."""
    return text.split("## Failures", 1)[1]


def test_403_is_failed_fetch_http_403(spider):
    spider.ledger.enqueue(URL)
    body = (FIXTURES / "ledger" / "forbidden.html").read_bytes()
    yielded = drain(spider.parse(FakeResponse(URL, status=403, body=body)))

    assert yielded == [], "no browsers: a 403 must not escalate"
    record = spider.ledger.records[URL]
    assert record["outcome"] == "failed_fetch"
    assert record["reason"] == "http 403"
    assert record["status"] == 403

    table = failures_table(report(spider))
    assert f"| {URL} | failed_fetch | static | http 403 |" in table


def test_thin_page_without_escalation_is_written_as_ok(spider):
    """The TRUE class for a 17-char page is `ok`, not skipped_no_content.

    THIN_CHARS gates ESCALATION only. When no browser tier exists, parse() falls
    through to _write_best(), which writes whatever the best tier extracted as long
    as it is non-empty — so a 17-char page lands as an `ok` record of 17 chars.
    Only a genuinely empty extraction reaches skipped_no_content (test below).
    """
    spider.ledger.enqueue(URL)
    body = (FIXTURES / "ledger" / "thin.html").read_bytes()
    yielded = drain(spider.parse(FakeResponse(URL, status=200, body=body)))

    assert yielded == [], "no browsers: a thin page must not escalate"
    record = spider.ledger.records[URL]
    assert record["outcome"] == "ok"
    assert record["chars"] == 17 < spider_mod.THIN_CHARS
    assert record["file"] == "guide-setup.md"

    text = report(spider)
    assert "| ok | 1 |" in text
    assert "None." in failures_table(text), "an ok page must not appear in the Failures table"


def test_empty_extraction_without_escalation_is_skipped_no_content(spider):
    spider.ledger.enqueue(URL)
    body = (FIXTURES / "ledger" / "empty-shell.html").read_bytes()
    yielded = drain(spider.parse(FakeResponse(URL, status=200, body=body)))

    assert yielded == [], "no browsers: a JS shell must not escalate"
    record = spider.ledger.records[URL]
    assert record["outcome"] == "skipped_no_content"
    assert record["reason"] == "no extractable content (none)"
    assert record["status"] == 200
    assert not list(Path(spider.out_dir).glob("*.md")), "an empty page must not be written"

    table = failures_table(report(spider))
    assert f"| {URL} | skipped_no_content | static |" in table


def test_exception_with_retries_exhausted_is_failed_exception(spider):
    spider.ledger.enqueue(URL)
    request = FakeRequest(URL, meta={"origin_url": URL, "retry": 1})
    asyncio.run(spider.on_error(request, TimeoutError("page load timed out")))

    record = spider.ledger.records[URL]
    assert record["outcome"] == "failed_exception"
    assert "TimeoutError" in record["reason"], "the reason must name the exception class"
    assert "page load timed out" in record["reason"]

    table = failures_table(report(spider))
    assert f"| {URL} | failed_exception | static | TimeoutError: page load timed out |" in table


def test_enqueued_but_never_answered_is_reconciled_into_the_report(spider):
    """reconcile() is the no-silent-failure guarantee: a dropped URL still gets a class."""
    dropped = "https://docs.example.test/guide/dropped"
    spider.ledger.enqueue(dropped)

    text = report(spider)
    record = spider.ledger.records[dropped]
    assert record["outcome"] == "failed_fetch"
    assert record["reason"] == "no response recorded (dropped after retries)"
    assert "Ledger reconciles: 1 recorded of 1 enqueued (OK — zero silent failures)" in text
    assert f"| {dropped} | failed_fetch | static |" in failures_table(text)

    manifest = json.loads((Path(spider.out_dir) / "manifest.json").read_text())
    assert manifest["pages"][0]["url"] == dropped


def test_all_three_failure_classes_coexist_in_one_report(spider):
    """A run mixing outcomes must show every class — no last-writer-wins in the table."""
    ok_url = "https://docs.example.test/guide/ok"
    for url in (URL, ok_url):
        spider.ledger.enqueue(url)
    drain(
        spider.parse(
            FakeResponse(
                ok_url,
                body=(FIXTURES / "negatives" / "plain.html").read_bytes(),
                meta={"depth": 0, "origin_url": ok_url},
            )
        )
    )
    drain(spider.parse(FakeResponse(URL, status=403, body=b"nope")))
    asyncio.run(spider.on_error(FakeRequest("https://docs.example.test/guide/boom",
                                            meta={"origin_url": "https://docs.example.test/guide/boom",
                                                  "retry": 1}),
                                RuntimeError("boom")))

    text = report(spider)
    assert "| ok | 1 |" in text
    assert spider.ledger.records[ok_url]["outcome"] == "ok"
    table = failures_table(text)
    assert "failed_fetch" in table and "failed_exception" in table
