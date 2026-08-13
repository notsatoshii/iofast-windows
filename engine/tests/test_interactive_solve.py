"""Interactive CAPTCHA handoff (Phase 4 slice #5).

Offline unit coverage — no browser is ever launched. scrapling's StealthySession
is stubbed with a recorder so these tests pin OUR orchestration (shared profile,
pinned UA, single proxy, the parse trigger's gating), not scrapling's browser
behaviour. The live headed→headless mechanism is exercised by the manual smoke
(tools/captcha_solve_smoke.py), not here.
"""
import asyncio
import threading

import scrapling.fetchers as fetchers

from iofast_engine import solve
from iofast_engine.solve import _human_solve, solve_and_continue
from iofast_engine.spider import DocSpider
from iofast_engine.tiers import is_challenge

START = "https://docs.example.com/docs/"
URL = "https://docs.example.com/docs/walled"


# ---------- fakes ----------


class FakePage:
    """Minimal playwright-page stand-in for _human_solve.

    ``htmls`` is the sequence returned by successive content() calls, so a test
    can model "challenge, challenge, cleared".
    """

    def __init__(self, htmls, ua="RealChrome/149"):
        self._htmls = list(htmls)
        self._ua = ua
        self.waits = 0

    def evaluate(self, _expr):
        return self._ua

    def content(self):
        return self._htmls.pop(0) if len(self._htmls) > 1 else self._htmls[0]

    def wait_for_timeout(self, _ms):
        self.waits += 1


class FakeResp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.body = body


CLEAN_BODY = ("<html><body><article>" + "Real documentation text. " * 40 +
              "</article></body></html>").encode()


def fake_stealthy(record, *, headed_page=None, continue_resp=None):
    """Return a StealthySession stand-in that records constructor kwargs and,
    when a page_action is passed (the headed solve), drives it with headed_page.
    """

    class Fake:
        def __init__(self, **kw):
            self.kw = kw
            record.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch(self, url, **kw):
            action = kw.get("page_action")
            if action is not None:  # the headed solve session
                action(headed_page)
                return FakeResp()
            return continue_resp  # the headless continue

    return Fake


# ---------- _human_solve poll loop ----------


def test_human_solve_detects_cleared_challenge():
    captured = {"ua": "", "solved": False}
    page = FakePage(["<p>just a moment...</p>", "<p>just a moment...</p>", "<h1>Docs</h1>"])
    _human_solve(page, timeout_s=5, cancel_event=None, captured=captured)
    assert captured["solved"] is True
    assert captured["ua"] == "RealChrome/149"


def test_human_solve_times_out_without_solve():
    captured = {"ua": "", "solved": False}
    page = FakePage(["<p>just a moment...</p>"])  # never clears
    _human_solve(page, timeout_s=0, cancel_event=None, captured=captured)
    assert captured["solved"] is False


# A custom-branded Cloudflare managed challenge, modelled on www.indeed.com as
# measured live 2026-08-13: body 27674 bytes, title "Security Check - Indeed.com",
# "just a moment..." absent from the WHOLE body, first cf_chl_opt at byte 14866 and
# /cdn-cgi/challenge-platform/ at 17209 — both past the old 8000-char scan window.
# The header said cf-mitigated: challenge, but a page cannot see response headers.
BRANDED_CHALLENGE = (
    "<html><head><title>Security Check - Example</title></head><body>"
    + "<div class='pad'>checking your browser</div>" * 400
    + "<script>window._cf_chl_opt={cvId:'3'};</script>"
    + "<script src='/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1'></script>"
    + "</body></html>"
)


def test_branded_challenge_is_detected_past_the_old_scan_window():
    """The marker's OFFSET is the defect, not its spelling: the tokens are present,
    they just sit deeper into the page than detection used to look."""
    assert "just a moment..." not in BRANDED_CHALLENGE.lower()
    assert BRANDED_CHALLENGE.lower().index("cf_chl_opt") > 8000, "fixture must reproduce the offset"
    assert is_challenge(403, {}, BRANDED_CHALLENGE)
    assert is_challenge(200, {}, BRANDED_CHALLENGE), "_human_solve's call shape: no headers, status 200"


def test_rebranded_challenge_is_caught_by_the_script_src_alone():
    """Covers the marker the branded fixture cannot prove, since that one also carries
    cf_chl_opt. This is the rotation the old detection would have missed silently:
    the copy and the cf_chl_* names both change, but the challenge still has to fetch
    its script from /cdn-cgi/challenge-platform/."""
    rebranded = (
        "<html><head><title>Verifying your request</title></head><body>"
        + "<div class='pad'>one moment</div>" * 400
        + "<script src='/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1'></script>"
        + "</body></html>"
    )
    assert "cf_chl" not in rebranded.lower() and "just a moment..." not in rebranded.lower()
    assert is_challenge(200, {}, rebranded)


def test_each_body_marker_stands_on_its_own():
    """One isolating body per marker. Without this the committed fixture — which
    carries "just a moment..." at byte 77 — masks the other two: deleting either
    from the marker list left the whole suite green (checked 2026-08-13)."""
    for marker in ("just a moment...", "cf_chl_opt", "/cdn-cgi/challenge-platform/"):
        assert is_challenge(200, {}, f"<html><body>{marker}</body></html>"), marker
    assert not is_challenge(200, {}, "<html><body><article>Real docs.</article></body></html>")


def test_human_solve_keeps_waiting_through_a_branded_challenge():
    """The user-visible failure this guards: miss the marker and the loop marks the
    page solved on its FIRST poll, so the visible window shuts before the human can
    touch it and the headless continue walks straight into the live challenge."""
    captured = {"ua": "", "solved": False}
    page = FakePage([BRANDED_CHALLENGE])  # never clears
    _human_solve(page, timeout_s=0.1, cancel_event=None, captured=captured)
    assert captured["solved"] is False
    assert page.waits > 0, "must keep polling while the challenge is still up"


def test_human_solve_aborts_on_cancel():
    captured = {"ua": "", "solved": False}
    cancel = threading.Event()
    cancel.set()
    page = FakePage(["<h1>Docs</h1>"])  # would solve, but cancel wins
    _human_solve(page, timeout_s=5, cancel_event=cancel, captured=captured)
    assert captured["solved"] is False


# ---------- solve_and_continue orchestration ----------


def test_solve_and_continue_pins_profile_ua_and_single_proxy(monkeypatch):
    record = []
    resp = FakeResp(status=200, body=CLEAN_BODY)
    page = FakePage(["<h1>Docs</h1>"])  # cleared immediately
    monkeypatch.setattr(
        fetchers, "StealthySession",
        fake_stealthy(record, headed_page=page, continue_resp=resp),
    )
    events = []
    out = solve_and_continue(URL, proxy="http://pinned:9", timeout=5, emit=events.append)

    assert out is resp
    headed, cont = record
    # Headed solve is visible, does NOT auto-solve, uses the single pinned proxy.
    assert headed["headless"] is False
    assert headed["solve_cloudflare"] is False
    assert headed["proxy"] == "http://pinned:9"
    # Continue is headless, same profile, the captured build UA, same single proxy.
    assert cont["headless"] is True
    assert cont["user_data_dir"] == headed["user_data_dir"], "must share one profile"
    assert cont["useragent"] == "RealChrome/149"
    assert cont["proxy"] == "http://pinned:9"
    assert any(e["type"] == "human_required" for e in events)


def test_solve_and_continue_no_proxy_omits_kwarg(monkeypatch):
    record = []
    page = FakePage(["<h1>ok</h1>"])
    monkeypatch.setattr(
        fetchers, "StealthySession",
        fake_stealthy(record, headed_page=page, continue_resp=FakeResp(body=CLEAN_BODY)),
    )
    solve_and_continue(URL, proxy=None, timeout=5, emit=lambda e: None)
    for kw in record:
        assert "proxy" not in kw


def test_solve_and_continue_returns_none_and_warns_on_timeout(monkeypatch):
    record = []
    page = FakePage(["<p>just a moment...</p>"])  # never clears
    monkeypatch.setattr(
        fetchers, "StealthySession",
        fake_stealthy(record, headed_page=page, continue_resp=FakeResp(body=CLEAN_BODY)),
    )
    events = []
    out = solve_and_continue(URL, proxy=None, timeout=0, emit=events.append)
    assert out is None
    assert len(record) == 1, "the headless continue must NOT run when unsolved"
    assert any(e["type"] == "warning" for e in events)


# ---------- the human_required / human_done pair (UI banner contract) ----------
#
# A UI banner announcing the visible browser window needs two things the crawl
# itself cannot supply: how long the human has, and when the wait is over. The
# crawl fetches other pages throughout the solve, so their events arrive during
# the wait and cannot mark its end. Both come from this pair.


def only(events, kind):
    matches = [e for e in events if e["type"] == kind]
    assert len(matches) == 1, f"expected exactly one {kind}, got {len(matches)}"
    return matches[0]


def test_human_required_carries_the_solve_deadline(monkeypatch):
    """The countdown reads this field. Without it the app would have to hardcode
    the engine's own default, and drift the day that default moves."""
    page = FakePage(["<h1>ok</h1>"])
    monkeypatch.setattr(
        fetchers, "StealthySession",
        fake_stealthy([], headed_page=page, continue_resp=FakeResp(body=CLEAN_BODY)),
    )
    events = []
    solve_and_continue(URL, timeout=42, emit=events.append)
    asked = only(events, "human_required")
    assert asked["timeout"] == 42
    assert asked["url"] == URL


def test_human_done_reports_a_solve(monkeypatch):
    page = FakePage(["<h1>ok</h1>"])
    monkeypatch.setattr(
        fetchers, "StealthySession",
        fake_stealthy([], headed_page=page, continue_resp=FakeResp(body=CLEAN_BODY)),
    )
    events = []
    solve_and_continue(URL, timeout=5, emit=events.append)
    assert only(events, "human_done")["solved"] is True
    kinds = [e["type"] for e in events]
    assert kinds.index("human_required") < kinds.index("human_done"), "banner opens before it closes"


def test_human_done_reports_an_unsolved_wait(monkeypatch):
    page = FakePage(["<p>just a moment...</p>"])  # never clears
    monkeypatch.setattr(
        fetchers, "StealthySession",
        fake_stealthy([], headed_page=page, continue_resp=FakeResp(body=CLEAN_BODY)),
    )
    events = []
    solve_and_continue(URL, timeout=0, emit=events.append)
    assert only(events, "human_done")["solved"] is False


def test_human_done_fires_even_when_the_browser_raises(monkeypatch):
    """The must-fail case for the banner: a headed browser is fragile, and a
    raised solve is the one path that would otherwise leave the banner stuck on
    screen for the rest of the run."""

    class Exploding:
        def __init__(self, **kw):
            raise RuntimeError("chromium refused to launch")

    monkeypatch.setattr(fetchers, "StealthySession", Exploding)
    events = []
    try:
        solve_and_continue(URL, timeout=5, emit=events.append)
    except RuntimeError:
        pass  # the spider catches this; the event must still have been sent
    assert only(events, "human_done")["solved"] is False


# ---------- spider wiring + parse trigger ----------


def build_spider(tmp_path, monkeypatch, *, interactive):
    monkeypatch.setattr(DocSpider, "_discover", lambda self: ([], "crawl"))
    spider = DocSpider(START, out_dir=str(tmp_path), resolve=False, interactive=interactive)
    spider.browsers = True  # pretend chromium is installed
    return spider


def test_spider_reads_interactive_and_single_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("IOFAST_PROXY", "http://a:9, http://b:9")
    spider = build_spider(tmp_path, monkeypatch, interactive=True)
    assert spider.interactive is True
    assert spider.solve_proxy == "http://a:9"  # first only, never the rotator


class _MetaResp(FakeResp):
    def __init__(self):
        super().__init__(status=200, headers={"cf-mitigated": "challenge"},
                         body=b"<html><body>just a moment...</body></html>")
        self.meta = {"origin_url": URL, "tier": "stealth", "escalated": True, "depth": 2}
        self.url = URL


def _drive(spider, resp):
    async def go():
        return [x async for x in spider.parse(resp)]

    return asyncio.run(go())


def test_parse_triggers_solve_when_interactive(tmp_path, monkeypatch):
    spider = build_spider(tmp_path, monkeypatch, interactive=True)
    calls = []

    async def fake_solve(url, depth, escalated):
        calls.append((url, depth, escalated))
        return True  # pretend the human solved and the page was written

    monkeypatch.setattr(spider, "_solve", fake_solve)
    out = _drive(spider, _MetaResp())
    assert out == []  # solved: returned without escalating or recording a failure
    assert calls == [(URL, 2, True)]
    assert URL not in spider.ledger.records  # no failed_fetch when solved


def test_parse_skips_solve_when_not_interactive(tmp_path, monkeypatch):
    spider = build_spider(tmp_path, monkeypatch, interactive=False)
    called = []
    monkeypatch.setattr(spider, "_solve", lambda *a: called.append(a))
    _drive(spider, _MetaResp())
    assert called == [], "no headed handoff on an unattended run"
    assert spider.ledger.records[URL]["outcome"] == "failed_fetch"


def test_solve_ingests_a_good_continue_response(tmp_path, monkeypatch):
    spider = build_spider(tmp_path, monkeypatch, interactive=True)
    monkeypatch.setattr(spider, "_solve_blocking",
                        lambda url: FakeResp(status=200, body=CLEAN_BODY))
    wrote = asyncio.run(spider._solve(URL, depth=1, escalated=True))
    assert wrote is True
    assert spider.written == 1
    assert spider.ledger.records[URL]["outcome"] in ("ok", "thin_escalated_ok")


def test_solve_returns_false_on_no_response(tmp_path, monkeypatch):
    spider = build_spider(tmp_path, monkeypatch, interactive=True)
    monkeypatch.setattr(spider, "_solve_blocking", lambda url: None)
    assert asyncio.run(spider._solve(URL, depth=1, escalated=True)) is False
    assert spider.written == 0


def test_solve_lock_serializes_concurrent_handoffs(tmp_path, monkeypatch):
    """At most one headed window open at a time: with concurrent_requests>1, two
    stealth-challenged pages must not pop two browsers. The lock serializes the
    blocking solve so the observed concurrency never exceeds 1.
    """
    import time

    spider = build_spider(tmp_path, monkeypatch, interactive=True)
    live = {"n": 0, "max": 0}

    def blocking(url):  # runs in the executor thread; models the open window
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])
        time.sleep(0.05)  # widen the overlap window
        live["n"] -= 1
        return FakeResp(status=200, body=CLEAN_BODY)

    monkeypatch.setattr(spider, "_solve_blocking", blocking)

    async def go():
        await asyncio.gather(
            spider._solve(URL, 1, True),
            spider._solve(URL + "/2", 1, True),
        )

    asyncio.run(go())
    assert live["max"] == 1, "lock must serialize headed handoffs (saw overlap)"


def test_solve_returns_false_when_still_challenged(tmp_path, monkeypatch):
    spider = build_spider(tmp_path, monkeypatch, interactive=True)
    monkeypatch.setattr(
        spider, "_solve_blocking",
        lambda url: FakeResp(status=200, headers={"cf-mitigated": "challenge"},
                             body=b"just a moment..."),
    )
    assert asyncio.run(spider._solve(URL, depth=1, escalated=True)) is False
