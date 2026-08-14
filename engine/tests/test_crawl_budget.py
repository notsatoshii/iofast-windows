"""The page budget counts pages that can become FILES, not urls tried.

Measured 2026-08-13 on redocly.github.io/redoc/3.x: the page publishes real
anchors to client-side routes its static host 404s, and every one of them used
to consume a page slot — a 5-page probe wrote 2 pages and exited 1.

The end-to-end cases lead deliberately. The predicate is the easy half; the risk
lives in ORDERING and REFILL — a freed slot is worthless unless a held-back link
is still around to spend it, and with concurrent_requests=4 the 404s and the
successes interleave. A unit test on _budget_spent() cannot see any of that, so
these drive a real DocSpider against a localhost fixture server.
"""

import asyncio
import http.server
import threading
from collections import deque

import pytest

from iofast_engine.ledger import Ledger
from iofast_engine.runner import run_scrape
from iofast_engine.spider import ATTEMPT_RATIO, DocSpider

PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<main><h1>{title}</h1>
<p>Substantial fixture prose so the static tier clears the thin-page band
without escalating anywhere. This paragraph, and the next one, exist purely
to carry enough characters through extraction that the page records as ok.</p>
<p>{body}</p>
{links}</main></body></html>"""


class Site:
    """An index whose DEAD links come first in the DOM, then its real pages.

    Dead-first is the adversarial order: the budget is exhausted by urls that
    cannot produce a file before the crawler ever sees a good one.
    """

    def __init__(self, good=6, dead=6):
        self.good = [f"/docs/good-{i}" for i in range(good)]
        self.dead = [f"/docs/dead-{i}" for i in range(dead)]
        links = "".join(f'<a href="{p}">{p}</a>' for p in self.dead + self.good)
        self.bodies = {
            "/docs/": PAGE.format(title="Home", body="index prose " * 20, links=links),
            **{
                p: PAGE.format(title=p, body=f"page {p} content " * 12, links="")
                for p in self.good
            },
        }

        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path if self.path in site.bodies else self.path + "/"
                if path not in site.bodies:
                    # Including /sitemap.xml and /robots.txt: no sitemap means
                    # discovery stays in crawl mode, which is what has the budget.
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = site.bodies[path].encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()


def _files(result):
    return {r["url"] for r in result["manifest"]["pages"] if r.get("file")}


@pytest.fixture
def static_only(monkeypatch):
    """A 404 escalates toward the stealth tier, so on a machine that HAS chromium
    the dead-link fixtures would boot a solve_cloudflare browser per 404 —
    minutes of work against localhost. CI installs no browsers; this makes the
    tests behave the same way everywhere."""
    monkeypatch.setattr("iofast_engine.spider.browsers_available", lambda: False)


@pytest.mark.integration
def test_dead_links_do_not_spend_the_page_budget(tmp_path, static_only):
    """THE REGRESSION: ask for 5 pages from a site with 6 dead links, get 5."""
    site = Site(good=6, dead=6)
    try:
        result = run_scrape(f"{site.base}/docs/", out=str(tmp_path), max_pages=5)
        written = _files(result)
        assert len(written) == 5, result["manifest"]["counts"]
        assert f"{site.base}/docs" in written  # the index the user asked for
        # Every file is a real page; not one dead url produced one.
        assert not [url for url in written if "/dead-" in url]
    finally:
        site.stop()


@pytest.mark.integration
def test_a_site_where_every_link_is_dead_still_terminates(tmp_path, static_only):
    """The anti-spin backstop: no good pages to refill with, so the cap ends it."""
    site = Site(good=0, dead=40)
    try:
        result = run_scrape(f"{site.base}/docs/", out=str(tmp_path), max_pages=5)
        pages = result["manifest"]["pages"]
        assert len(pages) <= 5 * ATTEMPT_RATIO, len(pages)
        assert "attempt cap" in result["manifest"]["info"]
        assert len(_files(result)) == 1  # only the index had content
    finally:
        site.stop()


@pytest.mark.integration
def test_budget_is_still_a_ceiling_on_a_healthy_site(tmp_path, static_only):
    """The freed-slot machinery must not overshoot when nothing ever fails."""
    site = Site(good=20, dead=0)
    try:
        result = run_scrape(f"{site.base}/docs/", out=str(tmp_path), max_pages=6)
        assert len(_files(result)) == 6
        assert result["exit_code"] == 0
    finally:
        site.stop()


# --- the predicate itself -----------------------------------------------------


class _Scheduler:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, request):
        self.enqueued.append(request)


class _Engine:
    """The doors _top_up reaches through, named as the real engine names them."""

    def __init__(self, pause_requested=False):
        self.scheduler = _Scheduler()
        self._pause_requested = pause_requested
        self.normalized = []

    def _normalize_request(self, request):
        # The real one resolves an empty sid to the default session; recording the
        # call is enough to prove _top_up does not skip it.
        self.normalized.append(request)


def _spider(max_pages=5, resume_pending=(), attempt_cap=0):
    """A budget-only spider: object.__new__ skips discovery and the fetch stack.

    Every attribute _top_up actually touches is set, so a removed guard fails on
    THIS FILE'S assertion rather than on an AttributeError somewhere downstream
    — a test that dies before reaching its assert proves nothing about the guard.
    """
    spider = object.__new__(DocSpider)
    spider.ledger = Ledger()
    spider.max_pages = max_pages
    spider._resume_pending = set(resume_pending)
    spider._attempt_cap = attempt_cap
    spider._attempt_cap_hit = False
    spider.deferred = deque()
    spider._defer_cap = max_pages * ATTEMPT_RATIO
    spider.events = []
    spider.emit = spider.events.append
    # The drain path: _enqueue -> started, _page_request -> md_map/_request_kwargs.
    spider.started = {}
    spider.md_map = {}
    spider.previous = {}
    spider.start_tier = "static"
    spider.resume = False
    spider.cancel_event = None
    spider._engine = None
    return spider


def _record(spider, url, outcome="ok", file="page.md"):
    spider.ledger.record(url, outcome, "", file=file, chars=1000)


def test_a_failed_page_hands_its_slot_back():
    spider = _spider(max_pages=5)
    for i in range(5):
        spider.ledger.enqueue(f"https://x/{i}")
    assert spider._budget_spent()
    for i in range(3):
        _record(spider, f"https://x/{i}", "failed_fetch", file="")
    assert not spider._budget_spent()


def test_a_written_page_keeps_its_slot():
    spider = _spider(max_pages=5)
    for i in range(5):
        _record(spider, f"https://x/{i}")
    assert spider._budget_spent()


def test_suspect_shrink_keeps_its_slot_because_it_keeps_its_file():
    # It refused to overwrite, so the previous run's file is still the corpus.
    spider = _spider(max_pages=3)
    for i in range(3):
        _record(spider, f"https://x/{i}", "suspect_shrink", file="kept.md")
    assert spider._budget_spent()


def test_resume_pending_urls_are_fetches_in_flight_not_free_slots():
    """Resume seeds the ledger with prior fileless rows while the checkpoint
    frontier is replaying those very urls — counting them free would enqueue a
    second crawl's worth on top of the one already draining."""
    pending = [f"https://x/{i}" for i in range(3)]
    spider = _spider(max_pages=5, resume_pending=pending)
    for url in pending:
        _record(spider, url, "cancelled", file="")
    for i in range(3, 5):
        _record(spider, f"https://x/{i}")
    assert spider._budget_spent()
    # ...until this run actually touches one and it fails again.
    spider._resume_pending.discard(pending[0])
    assert not spider._budget_spent()


def test_attempt_cap_trips_and_warns_exactly_once():
    spider = _spider(max_pages=5, attempt_cap=8)
    for i in range(8):
        _record(spider, f"https://x/{i}", "failed_fetch", file="")
    assert spider._budget_spent()  # cap wins even though every slot is free
    assert spider._budget_spent()
    assert len([e for e in spider.events if "attempt cap" in e["message"]]) == 1


def test_only_cancelled_prior_rows_count_as_replayed_frontier():
    """The checkpoint frontier holds the urls on_close recorded "cancelled". A
    prior failed_fetch was already FETCHED, so it is never replayed — treating it
    as pending would leave it occupying a slot forever and quietly restore the
    pre-fix behaviour on every resume."""
    previous = {
        "https://x/cancelled": {"outcome": "cancelled", "file": ""},
        "https://x/failed": {"outcome": "failed_fetch", "file": ""},
        "https://x/written": {"outcome": "ok", "file": "written.md"},
    }
    spider = object.__new__(DocSpider)
    spider.previous = previous
    spider.resume = True
    DocSpider._seed_resume_pending(spider)
    assert spider._resume_pending == {"https://x/cancelled"}


def test_the_hold_queue_keeps_its_HEAD_when_it_fills():
    """A bounded deque discards from the LEFT, which would silently swap the
    sitemap's head for its tail on a big site. _defer must stop appending."""
    spider = _spider(max_pages=2)  # cap = 2 * ATTEMPT_RATIO
    for i in range(spider._defer_cap + 10):
        spider._defer(f"https://x/{i}")
    assert len(spider.deferred) == spider._defer_cap
    assert spider.deferred[0] == ("https://x/0", False, 0)


def test_top_up_spends_a_freed_slot_on_a_held_url():
    spider = _spider(max_pages=2)
    spider._engine = _Engine()
    _record(spider, "https://x/written")
    _record(spider, "https://x/dead", "failed_fetch", file="")
    spider._defer("https://x/held")
    asyncio.run(spider._top_up())
    assert not spider.deferred
    assert "https://x/held" in spider.ledger.enqueued
    assert len(spider._engine.scheduler.enqueued) == 1
    # Skipping normalization would leave sid empty, and sid feeds the request
    # fingerprint the scheduler's checkpoint-restored seen-set matches on.
    assert spider._engine.normalized == spider._engine.scheduler.enqueued


def test_top_up_does_nothing_once_a_cancel_is_requested():
    """Stop means stop: a paused engine still checkpoints its queue, so topping
    up here would hand resume a frontier the user cancelled before it existed."""
    spider = _spider(max_pages=5)
    spider._engine = _Engine()
    spider.cancel_event = threading.Event()
    spider.cancel_event.set()
    spider._defer("https://x/held")
    asyncio.run(spider._top_up())
    assert list(spider.deferred) == [("https://x/held", False, 0)]
    assert not spider.ledger.enqueued
    assert not spider._engine.scheduler.enqueued


def test_top_up_also_respects_a_ctrl_c_pause_that_never_set_cancel_event():
    """A CLI Ctrl-C goes through scrapling's own SIGINT handler to
    _engine.request_pause() and never touches cancel_event, so keying the guard
    on cancel_event alone would still enqueue into a run that is stopping."""
    spider = _spider(max_pages=5)
    spider._engine = _Engine(pause_requested=True)
    spider._defer("https://x/held")
    asyncio.run(spider._top_up())
    assert list(spider.deferred) == [("https://x/held", False, 0)]
    assert not spider._engine.scheduler.enqueued


def test_no_attempt_cap_configured_never_trips():
    # Partial spiders (and any future caller that skips __init__) must not stop
    # on a cap of 0 — that reads as "not computed", not "zero attempts allowed".
    spider = _spider(max_pages=5, attempt_cap=0)
    _record(spider, "https://x/0", "failed_fetch", file="")
    assert not spider._budget_spent()
    assert not spider.events
