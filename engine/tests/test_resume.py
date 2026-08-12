"""Resume seeding: a paused crawl's checkpoint replays the frontier, but the
checkpoint does NOT carry the ledger — so DocSpider seeds ledger + seen from the
prior manifest, else on_close would reconcile only the resumed tail and the
manifest would undercount the whole crawl (docs/RPC.md, THE INVARIANT).

Offline: _discover is stubbed so no network is touched (the fetch stack is never
exercised in these unit tests). The must-fail guard cases live in
test_rpc_session.py alongside the other scrape param checks.
"""

import asyncio

from iofast_engine.spider import DocSpider

START = "https://docs.example.com/docs/"
URL_A = "https://docs.example.com/docs/a"
URL_B = "https://docs.example.com/docs/b"
URL_F = "https://docs.example.com/docs/f"  # a prior terminal FAILURE — no file

PREVIOUS = {
    URL_A: {
        "file": "a.md", "outcome": "ok", "reason": "", "tier": "static",
        "chars": 500, "status": 200, "escalated": False,
        "etag": '"a-v1"', "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT",
    },
    URL_B: {
        "file": "b.md", "outcome": "thin_escalated_ok", "reason": "", "tier": "dynamic",
        "chars": 800, "status": 200, "escalated": True,
        "etag": '"b-v1"', "last_modified": None,
    },
    # A fileless 404 from the prior run. On a pure link-crawl resume its only
    # inbound link may be on an already-fetched page, so it is never re-linked —
    # seeding MUST preserve it or the whole-crawl manifest silently loses it.
    URL_F: {
        "file": None, "outcome": "failed_fetch", "reason": "http 404",
        "tier": "static", "chars": 0, "status": 404,
    },
}
FILED = {URL_A, URL_B}  # the rows that carry a written file


def build_spider(tmp_path, monkeypatch, *, resume, previous):
    # No network: skip discovery and treat the start URL as already resolved.
    monkeypatch.setattr(DocSpider, "_discover", lambda self: ([], "crawl"))
    return DocSpider(
        START, out_dir=str(tmp_path), resolve=False, previous=previous, resume=resume
    )


def test_resume_seeds_ledger_seen_and_suppresses_conditionals(tmp_path, monkeypatch):
    spider = build_spider(tmp_path, monkeypatch, resume=True, previous=PREVIOUS)
    # EVERY prior row is recorded with its outcome — the fileless failure included.
    assert set(spider.ledger.records) == set(PREVIOUS)
    assert spider.ledger.records[URL_A]["outcome"] == "ok"
    assert spider.ledger.records[URL_B]["outcome"] == "thin_escalated_ok"
    assert spider.ledger.records[URL_F]["outcome"] == "failed_fetch"  # preserved, not dropped
    # Only WRITTEN pages are marked seen (don't re-fetch them). A fileless failure
    # stays un-seen so a re-linked retry can still happen and overwrite the record.
    assert set(spider.seen) >= FILED
    assert URL_F not in spider.seen
    # The written file's name -> url is re-registered so re-links resolve.
    assert spider.writer.names["a"] == URL_A
    # Resume must NOT send conditional GETs — it continues the frontier.
    kwargs = spider._request_kwargs({"origin_url": URL_A})
    assert "headers" not in kwargs


def test_non_resume_with_previous_still_sends_conditionals(tmp_path, monkeypatch):
    """The guard is resume-specific: an update-style spider (previous supplied,
    static start tier, resume=False) does NO seeding and DOES send validators."""
    spider = build_spider(tmp_path, monkeypatch, resume=False, previous=PREVIOUS)
    assert spider.ledger.records == {}  # no seeding on a non-resume run
    kwargs = spider._request_kwargs({"origin_url": URL_A})
    assert kwargs["headers"]["If-None-Match"] == '"a-v1"'


def test_resume_clamps_bad_outcome_and_keeps_fileless_rows(tmp_path, monkeypatch):
    x = "https://docs.example.com/docs/x"
    y = "https://docs.example.com/docs/y"
    z = "https://docs.example.com/docs/z"
    previous = {
        x: {"file": "x.md", "outcome": "bogus", "chars": 1},   # unknown class -> clamp ok
        y: {"file": "y.md", "outcome": None, "chars": 1},       # blank class -> clamp ok
        z: {"file": None, "outcome": "failed_fetch", "chars": 0},  # fileless failure -> kept
    }
    spider = build_spider(tmp_path, monkeypatch, resume=True, previous=previous)
    records = spider.ledger.records
    assert records[x]["outcome"] == "ok"
    assert records[y]["outcome"] == "ok"
    # Fileless failure is PRESERVED (not dropped) but NOT marked seen — a re-linked
    # retry can still overwrite it. Written pages x/y ARE seen.
    assert records[z]["outcome"] == "failed_fetch"
    assert z not in spider.seen
    assert {x, y} <= set(spider.seen)


def test_resume_manifest_reconciles_over_whole_crawl(tmp_path, monkeypatch):
    """THE INVARIANT: after a resumed tail lands, the final manifest covers the
    WHOLE crawl (seeded history + new pages) and still reconciles."""
    spider = build_spider(tmp_path, monkeypatch, resume=True, previous=PREVIOUS)
    spider.ledger.enqueue("https://docs.example.com/docs/c")
    spider.ledger.record("https://docs.example.com/docs/c", "ok", chars=300, file="c.md")
    asyncio.run(spider.on_close())
    assert spider.manifest["reconciled"] is True
    urls = {r["url"] for r in spider.manifest["pages"]}
    assert urls >= set(PREVIOUS) | {"https://docs.example.com/docs/c"}
