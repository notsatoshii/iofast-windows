"""DocSpider — the only module that imports scrapling at module level."""

import os
import sys
import re
import threading
import time

# Alias playwright → patchright BEFORE any scrapling fetcher/session import.
# Scrapling's AsyncDynamicSession hard-imports playwright.async_api; in frozen
# builds only patchright is bundled. The alias makes those imports resolve to
# our bundled driver. Must be at module top, before any .tiers / .sources import
# that might trigger scrapling loading.
from . import browser_helper  # noqa: F401 — side-effect: sys.modules alias
from collections import deque
from urllib.parse import urlparse

from scrapling.spiders import Request, Response, Spider

from .adapters.detection import detect_late, selectors_for
from .discovery import (
    ROBOTS_POLICY,
    Robots,
    _body_text,
    discover_llms,
    discover_sitemap,
    looks_like_markdown,
    probe_md_twin,
    resolve_start,
    robots_sitemaps,
)
from .extract import CONTENT_SELECTORS, page_content
from .ledger import OUTCOMES, Ledger
from .rescue import rescue_selectors
from .tiers import THIN_CHARS, TIERS, browsers_available, header_lookup, is_challenge
from .urls import (
    VERSION_ROOT_LOCALES,
    _md_canonical,
    bad_href,
    clean_url,
    md_twin_map,
    md_twins,
    md_variant,
    norm_host,
    path_depth,
    reject_url,
    scope_prefix,
    suffix_twin,
)
from .writer import Writer, write_run

# Overwrite guard for unattended update runs: refuse to replace a page when the
# new content is under this fraction of the ledger's recorded chars. A soft-block
# or consent page answers 200 with no Cloudflare markers and >= THIN_CHARS
# extracted (measured 2026-08-12: consent fixture extracts 357 chars; real
# fixture pages 731-1922, so a consent page lands at 0.19-0.49 of them — 0.3
# would let the 731-char case through). A refused page keeps its old file and is
# recorded "suspect_shrink"; a false positive costs one flagged, reviewable page.
UPDATE_SHRINK_RATIO = 0.5

# A page the site does not serve costs a fetch, not a page slot. The budget
# therefore counts pages that can still become FILES (enqueued minus the urls
# already resolved without one), and a link rejected at the budget is DEFERRED
# rather than dropped — a dropped link cannot fill the slot its sibling's 404
# frees. Measured 2026-08-13: redocly.github.io/redoc/3.x publishes real anchors
# to client-side routes its static host 404s, so a 5-page probe used to spend 3
# slots on them and write 2 pages. ATTEMPT_RATIO caps how many urls one run may
# enqueue (3x the pages asked for, i.e. it tolerates a 2:1 failure rate) so a
# site where EVERY link fails still terminates instead of walking the host.
ATTEMPT_RATIO = 3

# Below this many in-scope sitemap urls, discovery stays in crawl mode instead
# of trusting the sitemap as the whole frontier. Measured 2026-08-13: Read the
# Docs sitemaps list version roots only — sphinx-doc.org/sitemap.xml has exactly
# 1 <loc>, which used to collapse the crawl to a single landing page. Healthy
# docs sitemaps probed the same day (docusaurus.io, mkdocs-material, vitepress)
# all clear this floor by orders of magnitude.
SITEMAP_MIN_YIELD = 3

# RTD multi-version sitemaps list ONLY version roots — measured 2026-08-13:
# urllib3.readthedocs.io and docs.celeryq.dev both ship /en/latest, /en/stable,
# /en/2.7.0, ... (6+ locs, all roots, zero content pages), which clears the
# yield floor yet still collapses a root-start crawl to landing pages. A path
# shaped `/<locale>/<one-segment>` is a version root, never a doc page — and
# the first segment must be a REAL language code (urls.VERSION_ROOT_LOCALES),
# else healthy two-segment sitemaps like /go/install, /js/install would be
# misread as roots.
# Second segment must LOOK like a version too, else a flat /en/<page> docs site
# would have its whole sitemap misread as roots. Covers every measured RTD loc:
# latest, stable, dev, main, master, 2.7.0, 1.26.x, v2.
_VERSION_SEG_RE = re.compile(r"^(?:latest|stable|dev|main|master|v?\d[\w.]*)$")


def _version_root(path: str) -> bool:
    segments = [seg for seg in path.split("/") if seg]
    if len(segments) != 2:
        return False
    base = re.split(r"[-_]", segments[0], maxsplit=1)[0].lower()
    return base in VERSION_ROOT_LOCALES and bool(_VERSION_SEG_RE.match(segments[1].lower()))


def _proxy_kwarg(raw):
    """IOFAST_PROXY -> the proxy kwarg every fetch tier shares.

    One value -> ``{"proxy": ...}`` (byte-identical to the single-proxy MVP).
    Several (comma/whitespace/newline separated) -> ``{"proxy_rotator": ...}``
    holding ONE shared ProxyRotator; Scrapling cycles it per fetch across every
    tier. The rotation is cyclic, NOT failure-driven — a request through a dead
    proxy fails and the NEXT request just moves to the next in the ring.
    Scrapling raises if both ``proxy`` and ``proxy_rotator`` are passed
    (engines/static.py, _browsers/_validators.py), so exactly one key is set.
    None/blank -> ``{}`` (no proxy kwarg at all).
    """
    proxies = [p for p in (raw or "").replace(",", " ").split() if p]
    if len(proxies) == 1:
        return {"proxy": proxies[0]}
    if len(proxies) > 1:
        from scrapling.fetchers import ProxyRotator

        return {"proxy_rotator": ProxyRotator(proxies)}
    return {}


def _single_proxy(raw):
    """First IOFAST_PROXY value, or None. The interactive solve+continue pair
    pins ONE non-rotating proxy (cf_clearance is IP-bound), so it cannot use the
    per-fetch rotator _proxy_kwarg builds for the tiers.
    """
    proxies = [p for p in (raw or "").replace(",", " ").split() if p]
    return proxies[0] if proxies else None


class _BrowserHelperSession:
    """Minimal scrapling-compatible session that delegates to browser_helper.

    Used in frozen builds where scrapling's AsyncDynamicSession cannot find
    the node driver through its internal transport layer. browser_helper's
    async_fetch_with_browser launches browsers directly via patchright with
    system channel fallback, which works reliably in PyInstaller bundles.
    """

    def __init__(self, *, timeout_ms: int = 15000):
        self._timeout_ms = timeout_ms
        self._is_alive = False

    async def start(self):
        self._is_alive = True

    async def close(self):
        self._is_alive = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def fetch(self, url: str, **kwargs):
        from scrapling.spiders import Response
        from .browser_helper import async_fetch_with_browser

        html, info = await async_fetch_with_browser(url, timeout_ms=self._timeout_ms)
        if html is None:
            raise RuntimeError(f"Browser fetch failed for {url}: {info}")

        return Response(
            url=url,
            content=html,
            status=200,
            reason="OK",
            cookies={},
            headers={},
            request_headers={"referer": "https://www.google.com/"},
            encoding="utf-8",
            method="GET",
        )


class DocSpider(Spider):
    name = "iofast_docs"

    # Politeness: upstream defaults (0.0s delay, unlimited per-domain) are unsafe unattended.
    download_delay = 0.5
    concurrent_requests = 4
    concurrent_requests_per_domain = 2

    # Class-level defaults so partially-constructed spiders (tests build via
    # object.__new__) read as "no cancel requested, no previous run".
    cancel_event = None
    _pause_sent = False
    _solve_lock = None  # lazily created asyncio.Lock: one headed handoff at a time
    interactive = False  # instances set their own; default = no human CAPTCHA handoff
    solve_proxy = None
    resume = False  # instances set their own; default keeps fresh runs unchanged
    previous: dict = {}  # read-only fallback; instances get their own in __init__
    validators = None  # lazy: written only through _remember_validators
    # Budget state, class-level so partially-constructed spiders read as a fresh
    # run with nothing held back (tests build via object.__new__).
    # Read-only in practice: _defer appends only while len < _defer_cap, and the
    # 0 default means a spider that skipped __init__ can never mutate the shared
    # deque. Instances get their own in __init__.
    deferred: deque = deque()
    _defer_cap = 0  # instances size it from max_pages; 0 = hold nothing
    _resume_pending: set = set()  # only ever discarded from, never added to
    _attempt_cap = 0  # 0 = no run-scoped cap computed yet (partial spiders)
    _attempt_cap_hit = False

    def __init__(
        self,
        start_url,
        max_depth=3,
        max_pages=200,
        out_dir=None,
        scope=None,
        emit=None,
        adapter=None,
        resolve=True,
        cancel_event=None,
        previous=None,
        resume=False,
        interactive=False,
        **kwargs,
    ):
        self.emit = emit or (lambda event: None)
        # Cooperative cancel (RPC): the event is set by another thread; parse()
        # checks it as a backstop for cancels that land before the engine exists.
        self.cancel_event = cancel_event
        self._pause_sent = False
        self._cancel_lock = threading.Lock()
        # Interactive CAPTCHA handoff: on a challenge the stealth tier can't
        # auto-solve, open a visible browser for a one-time human solve (solve.py).
        # Default off — scheduled/unattended runs never block on a human. The
        # solve+continue pins ONE proxy (cf_clearance is IP-bound), so it takes
        # the first IOFAST_PROXY value, never the tiers' rotator.
        self.interactive = bool(interactive)
        self.solve_proxy = _single_proxy(os.environ.get("IOFAST_PROXY"))
        # The CLI resolves and fingerprints the start URL once, then passes both in.
        resolved = resolve_start(start_url, emit=self.emit) if resolve else start_url
        parsed = urlparse(resolved)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.host = parsed.netloc
        self.allowed_domains = {norm_host(self.host)}
        self.start_url = clean_url(resolved) or self.origin
        # The user-supplied path anchors the scope even when the host redirected.
        # A trailing slash means "this directory", so derive the scope before cleaning.
        self.scope = scope or scope_prefix(resolved)
        self.keep_locales = frozenset(
            seg for seg in urlparse(start_url).path.strip("/").split("/") if seg
        )
        self.max_depth = int(max_depth)
        self.max_pages = int(max_pages)

        self.out_dir = out_dir or f"docs-{self.host.replace('.', '-')}"
        os.makedirs(self.out_dir, exist_ok=True)

        self.seen = set()
        self.ledger = Ledger()
        self.writer = Writer(self.out_dir, self.ledger, max_pages=self.max_pages, emit=self.emit)
        self.robots = Robots()
        self.best = {}  # url -> (chars, text, tier, how, escalated, status)
        self.attempts = {}  # url -> set of tiers already tried (anti-ping-pong)
        self.started = {}  # url -> monotonic clock at enqueue
        self.manifest = {}
        self.adapter = adapter
        self.rescue_attempted = False
        self.rescued = None  # selectors adopted from the Tier 3 LLM rescue, if any
        self.last_status = {}  # url -> last HTTP status seen (for exhausted retries)
        # Re-scrape support: previous manifest rows (url -> row) drive conditional
        # GETs; validators captures this run's ETag/Last-Modified for the next one.
        self.previous = previous or {}
        self.validators = {}  # url -> (etag, last_modified)
        self.resume = bool(resume)
        if self.resume:
            # Resume replays the checkpoint frontier, but the checkpoint does NOT
            # carry the ledger — so seed both ledger and seen from the prior
            # manifest, else on_close would reconcile only the resumed tail and
            # the manifest would undercount the whole crawl. Mirrors the 304 path.
            for url, row in self.previous.items():
                outcome = row.get("outcome") or "ok"
                if outcome not in OUTCOMES:  # clamp a stale/blank class to ok
                    outcome = "ok"
                self.ledger.record(
                    url,
                    outcome,
                    row.get("reason", ""),
                    tier=row.get("tier", "static"),
                    chars=row.get("chars", 0),
                    file=row.get("file", "") or "",
                    status=row.get("status"),
                    escalated=row.get("escalated", False),
                    etag=row.get("etag"),
                    last_modified=row.get("last_modified"),
                )
                # A fileless prior FAILURE is preserved in the ledger but NOT marked
                # seen — so if the resumed frontier re-links it, it retries and
                # record() overwrites the placeholder. A written page IS seen (never
                # re-fetch it) and its name->url is re-registered so re-links resolve.
                if row.get("file"):
                    self.seen.add(url)
                    self.writer.names[row["file"].removesuffix(".md")] = url
        # In-scope urls the budget held back, oldest first; _top_up drains them
        # as pages resolve without a file. Bounded by _defer rather than by
        # deque(maxlen=...): a full bounded deque discards from the LEFT, so a
        # 10k-url sitemap would silently leave the TAIL of the list held and the
        # head dropped. Keeping the first arrivals matches seeds[:max_pages],
        # which already takes the head.
        self.deferred = deque()
        self._defer_cap = max(1, self.max_pages * ATTEMPT_RATIO)
        self._seed_resume_pending()
        self._attempt_cap_hit = False
        # Per-run ceiling: measured from where this run STARTED, else a few
        # pause/resume cycles would trip the cap on inherited history alone.
        self._attempt_cap = len(self.ledger.enqueued) + self.max_pages * ATTEMPT_RATIO
        self.selectors = selectors_for(adapter, CONTENT_SELECTORS)
        self.browsers = browsers_available()
        # The adapter's fetch_strategy is the STARTING tier; the ladder still runs above it.
        self.start_tier = adapter.fetch_strategy if adapter else "static"
        if self.start_tier != "static" and not self.browsers:
            self.emit(
                {
                    "type": "warning",
                    "message": (
                        f"⚠️  {adapter.name} wants the {self.start_tier} tier but no "
                        "chromium is installed — starting static"
                    ),
                }
            )
            self.start_tier = "static"
        if not self.browsers:
            self.emit(
                {
                    "type": "warning",
                    "message": (
                        "⚠️  no Playwright chromium found (looked under "
                        f"{os.environ.get('PLAYWRIGHT_BROWSERS_PATH', 'unset')})"
                        " — static tier only"
                    ),
                }
            )
        self.md_map = {}  # canonical page url -> exact twin url (_discover fills it)
        self.md_on = False  # run-wide md-twin capability (_discover decides)
        self.seeds, self.discovery = self._discover()
        self.start_urls = [self.start_url]
        # Pause saves a checkpoint under .crawl (Stop must never lose the run);
        # a FRESH run clears any stale one — auto-resume would skip
        # start_requests() and this Ledger is not part of the checkpoint.
        crawl_dir = os.path.join(self.out_dir, ".crawl")
        stale = os.path.join(crawl_dir, "checkpoint.pkl")
        if not resume and os.path.exists(stale):
            os.remove(stale)
        super().__init__(crawldir=crawl_dir, **kwargs)

    @property
    def written(self):
        return self.writer.written

    def configure_sessions(self, manager):
        """Static first; browser tiers are lazy so they only boot when escalated to.

        The browser-session import stays behind the guard: importing it pulls in
        playwright, which a frozen static-only sidecar deliberately excludes.

        IOFAST_PROXY (request-scoped, read from the environment like the rescue
        key) routes every tier through the resilient-fetch layer for sites that
        block by IP. One proxy = every tier uses it; several (comma/whitespace/
        newline separated) = one shared ProxyRotator cycled per fetch across all
        tiers (see _proxy_kwarg). Unset = no proxy kwarg, byte-identical to before.

        Frozen builds use _BrowserHelperSession instead of scrapling's native
        sessions: scrapling's launch_persistent_context resolves the node driver
        through a code path our sys.modules alias cannot intercept in PyInstaller
        bundles. browser_helper.async_fetch_with_browser works reliably because
        it launches browsers directly via patchright with system channel fallback.
        """
        from scrapling.fetchers import FetcherSession

        proxy_kw = _proxy_kwarg(os.environ.get("IOFAST_PROXY"))

        manager.add("static", FetcherSession(timeout=30, **proxy_kw), default=True)
        if self.browsers:
            if getattr(sys, "frozen", False):
                # Frozen: use our own session that delegates to browser_helper.
                manager.add(
                    "dynamic",
                    _BrowserHelperSession(timeout_ms=15000),
                    lazy=True,
                )
                manager.add(
                    "stealth",
                    _BrowserHelperSession(timeout_ms=15000),
                    lazy=True,
                )
            else:
                from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession

                manager.add(
                    "dynamic",
                    AsyncDynamicSession(
                        headless=True, disable_resources=True, real_chrome=True,
                        max_pages=2, **proxy_kw
                    ),
                    lazy=True,
                )
                manager.add(
                    "stealth",
                    AsyncStealthySession(
                        headless=True, solve_cloudflare=False, real_chrome=True,
                        max_pages=1, **proxy_kw
                    ),
                    lazy=True,
                )

    async def is_blocked(self, response):
        """Let upstream retry only transient codes; 401/403/407 escalate the tier instead."""
        blocked = response.status in (429, 500, 502, 503, 504)
        if blocked:
            # Upstream drops the request after its retries with no callback — remember
            # the status so on_close can report "http 429", not "no response recorded".
            meta = getattr(response, "meta", None) or {}
            url = meta.get("origin_url", clean_url(response.url))
            self.last_status[url] = response.status
        return blocked

    def _escalate(self, url, current, wanted, depth, reason):
        """Monotonic tier bump: never retries a lower tier, max one attempt per tier."""
        if not self.browsers:
            return None
        tried = self.attempts.setdefault(url, {current})
        index = max(TIERS.index(current) + 1, TIERS.index(wanted))
        while index < len(TIERS) and TIERS[index] in tried:
            index += 1
        if index >= len(TIERS):
            return None
        tier = TIERS[index]
        tried.add(tier)
        self.emit(
            {
                "type": "progress",
                "url": url,
                "message": f"⬆️  {reason}: {current} -> {tier} {url}",
            }
        )
        return Request(
            url,
            sid=tier,
            dont_filter=True,
            meta={"depth": depth, "origin_url": url, "tier": tier, "escalated": True},
        )

    def request_cancel(self):
        """Idempotent cancel, safe from any thread and lifecycle stage.

        pause() raises before the engine exists (RuntimeError) or before
        Spider.__init__ ran (AttributeError); both are swallowed because the
        parse-time backstop retries once the engine is live. _pause_sent is
        only set on success so exactly ONE cancel sequence is ever sent.

        Unlike CLI Ctrl+C (which goes through scrapling's SIGINT handler and
        lets the user press Ctrl+C again to escalate), RPC cancel is an
        explicit user action that must terminate promptly. We call pause()
        twice in succession: the first sets _pause_requested (graceful), the
        second sets _force_stop (immediate). As a belt-and-suspenders measure
        we also directly set the engine's _force_stop and _running flags —
        on Windows frozen builds the anyio cancel_scope.cancel() triggered
        by pause() may not interrupt in-flight I/O, so the engine loop must
        see these flags on its next iteration to exit promptly.
        """
        with self._cancel_lock:
            if self._pause_sent:
                return
            try:
                self.pause()  # first: _pause_requested = True
                self.pause()  # second: _force_stop = True (escalate immediately)
                self._pause_sent = True
            except (RuntimeError, AttributeError):
                pass
            # Direct flag injection: the async engine loop checks _force_stop
            # and _running on every iteration. Setting them here guarantees the
            # loop exits even if cancel_scope.cancel() doesn't interrupt the
            # current await (observed on Windows frozen builds where anyio's
            # ProactorEventLoop backend doesn't propagate cancellation to
            # curl_cffi/httpx fetch calls).
            engine = getattr(self, "_engine", None)
            if engine is not None:
                try:
                    engine._force_stop = True
                    engine._pause_requested = True
                    engine._running = False
                except Exception:
                    pass

    async def _solve(self, url, depth, escalated):
        """Interactive CAPTCHA handoff: run the blocking headed solve off the
        asyncio loop, then ingest the headless continue's response. Returns True
        iff a page was written. Sync playwright cannot run inside the running
        loop, so the solve goes to an executor thread (which has none). The
        solved page is treated as a leaf — its links are NOT followed (a page
        that needed a human is rescued content, not a crawl frontier).
        """
        import asyncio

        loop = asyncio.get_running_loop()
        if self._solve_lock is None:
            # Created here (not __init__): the spider is constructed off-loop.
            # The check-and-set has no await between, so it is atomic w.r.t. the
            # single-threaded event loop — no double-create race.
            self._solve_lock = asyncio.Lock()
        try:
            async with self._solve_lock:  # one headed handoff at a time
                resp = await loop.run_in_executor(None, self._solve_blocking, url)
        except Exception as exc:  # a headed browser is fragile; never crash the crawl
            self.emit({"type": "warning", "url": url, "message": f"⚠️  solve failed: {exc}"})
            return False
        if resp is None or resp.status != 200:
            return False
        headers = dict(resp.headers or {})
        body = _body_text(resp)
        if is_challenge(resp.status, headers, body):
            return False
        content, how = page_content(body, url, self.selectors)
        if len(content) < THIN_CHARS:
            return False
        self._write(url, content, "stealth", f"human-solved:{how}", escalated, resp.status)
        return True

    def _solve_blocking(self, url):
        from .solve import solve_and_continue

        return solve_and_continue(
            url, proxy=self.solve_proxy, cancel_event=self.cancel_event, emit=self.emit
        )

    def reject(self, url: str):
        return reject_url(url, self.host, self.scope, self.max_depth, self.keep_locales)

    def _exclude(self, reason):
        self.ledger.exclude(reason)

    def _enqueue(self, url):
        self.ledger.enqueue(url)
        self.started.setdefault(url, time.monotonic())

    def _seed_resume_pending(self):
        """The prior-run urls the checkpoint frontier is about to replay.

        Resume is the one case where the ledger already holds urls THIS run has
        not resolved: scrapling skips start_requests() and replays the checkpoint
        frontier instead (spiders/engine.py, "Resuming from checkpoint"), while
        __init__ seeded the ledger from every prior row. Those replayed urls are
        fetches in flight, not free slots — counting them as free would let a
        resumed run enqueue a second crawl's worth on top of the frontier it is
        already draining. Each drops out of the set the moment this run touches it
        (parse/on_error), so one that fails again does hand its slot back.

        "cancelled" is the exact class, NOT every fileless row: on_close records
        enqueued-but-never-fetched urls as cancelled, and that is precisely what
        the frontier holds. A prior failed_fetch WAS fetched, so it is never
        replayed — treating it as pending would leave it occupying a slot forever
        and quietly restore the pre-fix behaviour on every resume.
        """
        self._resume_pending = (
            {url for url, row in self.previous.items() if row.get("outcome") == "cancelled"}
            if self.resume
            else set()
        )

    def _budget_spent(self):
        """True when no further url may be enqueued right now.

        Two independent ceilings. The page budget counts urls that can still
        become files, so a page resolved WITHOUT one hands its slot back (see
        ATTEMPT_RATIO); `not r["file"]` is the exact discriminant — every
        outcome that produces a file keeps its slot, `suspect_shrink` included,
        because it deliberately keeps the previous run's file. The attempt cap is
        the anti-spin backstop and is absolute: once it trips, deferral stops too.
        """
        if self._attempt_cap and len(self.ledger.enqueued) >= self._attempt_cap:
            if not self._attempt_cap_hit:
                self._attempt_cap_hit = True
                self.emit(
                    {
                        "type": "warning",
                        "message": (
                            f"🛑 attempt cap: {self._attempt_cap} urls tried for "
                            f"{self.max_pages} pages — stopping the frontier"
                        ),
                    }
                )
            return True
        fileless = sum(
            1
            for url, record in self.ledger.records.items()
            if not record.get("file") and url not in self._resume_pending
        )
        return len(self.ledger.enqueued) - fileless >= self.max_pages

    def _defer(self, url, md_capable=False, depth=0):
        """Hold an in-scope url the budget had no room for. Never drop it here.

        The whole point of freeing a failed page's slot is that something is left
        to fill it, and the link loop sees each href exactly once — a page that
        404s is usually discovered on the same nav as the pages that succeed.
        """
        if not self._attempt_cap_hit and len(self.deferred) < self._defer_cap:
            self.deferred.append((url, md_capable, depth))

    async def _top_up(self):
        """A page resolved without a file — spend the freed slot on a held url.

        Straight to the scheduler, the same door on_error's retry uses: these
        urls already passed reject() and robots, they only lacked a slot.

        Never while a pause is pending. A paused engine stops dequeuing but still
        checkpoints its queue, so a top-up here would both manufacture extra
        "cancelled" rows in on_close's sweep and hand resume a frontier the user
        stopped before it existed. Both doors are checked: the RPC sets
        cancel_event, while a CLI Ctrl-C goes through scrapling's own SIGINT
        handler straight to _engine.request_pause() and never touches ours
        (spiders/spider.py _setup_signal_handler).
        """
        if self._paused_or_cancelled():
            return
        while self.deferred and self._engine and not self._budget_spent():
            url, md_capable, depth = self.deferred.popleft()
            self._enqueue(url)
            request = self._page_request(url, md_capable, depth)
            # The engine normalizes every request it enqueues itself, resolving an
            # empty sid to the default session. sid feeds Request's fingerprint,
            # so skipping it would give a topped-up url a different fingerprint
            # from the engine-routed one — and the scheduler's seen-set, restored
            # from checkpoint.pkl on resume, would then fetch it twice.
            self._engine._normalize_request(request)
            await self._engine.scheduler.enqueue(request)

    def _paused_or_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        return bool(getattr(self._engine, "_pause_requested", False))

    def _page_request(self, url, md_capable, depth):
        """One page fetch: the md twin when this run has one, else the page itself."""
        twin = self.md_map.get(url) if md_capable else None
        if twin is None and md_capable:
            # Derive a twin only for extension-less paths the llms.txt did not
            # list: `<page>.html.md` (Sphinx-shape urls) and the site root
            # twin-to-itself are guaranteed wasted fetches.
            last_segment = urlparse(url).path.rsplit("/", 1)[-1]
            candidate = md_variant(url)
            if "." not in last_segment and candidate != url:
                twin = candidate
        return Request(
            twin or url,
            **self._request_kwargs(
                {"depth": depth, "origin_url": url, "md_probe": twin is not None}
            ),
        )

    def _elapsed(self, url):
        start = self.started.get(url)
        return None if start is None else int((time.monotonic() - start) * 1000)

    def _discover(self):
        scope_root = f"{self.origin}{self.scope.rstrip('/')}"
        # Prefetch robots once for the origin so the seed reconcile never races it.
        self.robots.allowed(f"{self.origin}/")

        pages = discover_sitemap(self.origin, robots_sitemaps(self.origin), emit=self.emit)
        # An llms.txt means every page has a `<path>.md` twin (Mintlify / llms
        # convention) — the clean markdown source, regardless of whether the
        # sitemap or llms.txt supplied the url list. Probe it either way so
        # sitemap-discovered pages fetch the twin instead of scraping the SPA
        # HTML (which mangles down to a fragment). llms.txt links already point
        # at `.md`; canonicalise to the page url so naming/scope stay clean and
        # md_variant re-derives the same twin.
        llms_url, found = discover_llms(self.origin, scope_root, emit=self.emit)
        # Only per-page `.md` links prove twins exist. A generic llms.txt (bundle
        # files like llms-full.txt, no per-page `.md`) must NOT flip md on: else
        # every page double-fetches a 404ing twin and the bundle urls pollute the
        # crawl. Canonicalise the twins to their page url so naming/scope stay
        # clean and md_variant re-derives the same twin.
        canon = md_twins(found)
        # The llms.txt link is the ground-truth twin url; md_variant is only the
        # fallback for pages the llms.txt did not list (twin conventions differ:
        # `<path>.md` on Mintlify/ReadMe vs `<path>/index.md` on Cloudflare).
        self.md_map = md_twin_map(found)
        md = bool(canon)
        if found and not md:
            # ReadMe-style llms.txt: an index of PLAIN page urls whose `.md`
            # twins exist anyway (docs.readme.com serves both). Prove the
            # convention once against a real in-scope page before flipping md
            # on — a bundle-only llms.txt (svelte.dev) fails the probe and
            # stays md-off exactly as before.
            in_scope = [
                u for u in (clean_url(x) for x in found) if u and not self.reject(u)
            ]
            # Sample must have a real twin to probe (md_variant leaves the site
            # root unchanged — probing it would fetch HTML and wrongly keep md
            # off for the whole run) and the probe respects robots like any
            # other page fetch.
            sample = next(
                (
                    md_variant(u)
                    for u in in_scope
                    # Same shape rule as the seed twin derivation: an
                    # extension-bearing path (<page>.html) has no <page>.html.md
                    # twin — probing one would 404 and wrongly turn md off for
                    # the whole run.
                    if "." not in urlparse(u).path.rsplit("/", 1)[-1]
                    and md_variant(u) != u
                    and self.robots.allowed(md_variant(u))
                ),
                None,
            )
            if sample and probe_md_twin(sample):
                md = True
                canon = in_scope
        self.md_on = md

        if pages:
            urls = self._reconcile([url for url, _ in pages] + canon)
            self.emit(
                {
                    "type": "progress",
                    "message": (
                        f"🗺  sitemap: {len(pages)} urls, {len(urls)} in scope {self.scope}"
                        + (" · llms.txt .md twins" if md else "")
                    ),
                }
            )
            roots_only = bool(urls) and all(
                _version_root(urlparse(u).path) for u in urls
            )
            if roots_only:
                # Any number of version roots is still zero content pages —
                # same collapse as the sparse case below, just past the floor.
                self.emit(
                    {
                        "type": "progress",
                        "message": (
                            f"🔗 sitemap lists only version roots ({len(urls)}) — link crawl tops up"
                        ),
                    }
                )
                return [(url, md) for url in urls], "crawl"
            if len(urls) >= SITEMAP_MIN_YIELD:
                return [(url, md) for url in urls], ("sitemap+llms" if md else "sitemap")
            if urls:
                # Accepted gap: with md on, twin pages return before link
                # extraction, so the crawl top-up only works off HTML-fetched
                # pages. Sparse sitemap + md twins together hasn't been seen
                # live; if it shows up, prefer coverage (md off) here.
                # Degenerate sitemap: Read the Docs sitemaps list VERSION ROOTS
                # only (measured: sphinx-doc.org sitemap.xml = exactly 1 <loc>),
                # so trusting it would collapse the crawl to one landing page.
                # Keep the urls as seeds but stay in crawl mode so link
                # discovery tops the frontier up.
                # ponytail: floor catches 1-2 roots; a wide-scoped multi-version
                # RTD site (>2 version roots in scope) still trusts its sitemap —
                # detect "all locs are roots" if that ever bites.
                self.emit(
                    {
                        "type": "progress",
                        "message": (
                            f"🔗 sitemap too sparse ({len(urls)} urls) — link crawl tops up"
                        ),
                    }
                )
                return [(url, md) for url in urls], "crawl"

        if found:
            # `.md`-twin llms.txt (Mintlify): fetch the twins. Generic llms.txt:
            # take its raw urls as plain html seeds (md off, no bogus twin fetch).
            urls = self._reconcile(canon if md else found)
            self.emit(
                {
                    "type": "progress",
                    "message": (
                        f"📄 {llms_url}: {len(found)} urls, {len(urls)} in scope {self.scope}"
                    ),
                }
            )
            if urls:
                return [(url, md) for url in urls], "llms.txt"

        self.emit(
            {
                "type": "progress",
                "message": f"🔗 no sitemap/llms.txt in scope {self.scope} — link crawl",
            }
        )
        return [], "crawl"

    def _reconcile(self, urls):
        """Keep only in-scope URLs, deduped, counting every drop."""
        kept, kept_set = [], set()  # the set is the membership test; the list keeps order
        for url in urls:
            url = clean_url(url)
            if url in kept_set:
                continue
            reason = self.reject(url) or (
                None if self.robots.allowed(url) else "robots_disallow"
            )
            if reason:
                self._exclude(reason)
            else:
                kept.append(url)
                kept_set.add(url)
        # Second pass: -cn style locale-suffix twins (foo-cn beside foo) need the
        # full discovered set to detect, so they can't drop in the loop above.
        survivors = []
        for url in kept:
            if suffix_twin(url, kept_set, self.keep_locales):
                self._exclude("locale_suffix_twin")
            else:
                survivors.append(url)
        return survivors

    async def start_requests(self):
        # The user's start URL is a promise — a sitemap that omits it (RTD
        # sitemaps list version roots only) must not silently drop the one page
        # they explicitly asked for. But it is still subject to robots and the
        # scope filters like every other fetch — including on the empty-seeds
        # crawl fallback, where a Disallow: / must yield an honest empty run,
        # not a fetch the receipt claims we never make.
        start_ok = not self.reject(self.start_url) and self.robots.allowed(self.start_url)
        seeds = list(self.seeds)
        if not seeds:
            seeds = [(self.start_url, False)] if start_ok else []
        elif self.start_url in {url for url, _ in seeds}:
            # Front of the frontier, not merely present — a max_pages
            # truncation must never cut the one page the user asked for.
            seeds.sort(key=lambda seed: seed[0] != self.start_url)
        elif start_ok:
            seeds.insert(0, (self.start_url, seeds[0][1]))
        if self.previous and not self.resume:
            # docs/RPC.md `update`: the previous manifest seeds the url list.
            # Without this, a link-crawl site whose root answers 304 (empty
            # body, so no links to follow) never revisits its child pages, and
            # the rewritten manifest silently drops their validators.
            seeded = {url for url, _ in seeds}
            # A prior url that is only another name for a seeded page (an old
            # `/readme` twin row, now folded into `/`) would refetch, redirect to
            # an already-seen page, and land as a false failed_fetch.
            canon = {clean_url(url) for url in seeded}
            # A previous-manifest page keeps its twin when this run is
            # md-capable — else an update on a twin site would overwrite
            # markdown-sourced files with HTML extracts (or trip the shrink
            # guard). md_on, not md_map: the probe-proven class (ReadMe plain
            # links) has md on with an empty map.
            seeds = seeds + [
                (url, self.md_on)
                for url, row in sorted(self.previous.items())
                # robots re-checked: the site may have tightened its policy
                # since the prior run wrote these pages.
                if row.get("file")
                and url not in seeded
                and clean_url(_md_canonical(clean_url(url) + ".md")) not in canon
                and self.robots.allowed(url)
            ]
        for url, md_capable in seeds[: self.max_pages]:
            if url in self.seen:
                continue
            self.seen.add(url)
            self._enqueue(url)
            yield self._page_request(url, md_capable, 0)
        # The seeds past the budget are HELD, not discarded: on a sitemap or
        # llms.txt run parse() never reaches the link loop, so this is the only
        # place those discovery modes can refill a slot a dead url frees.
        for url, md_capable in seeds[self.max_pages :]:
            if url not in self.seen:
                self.seen.add(url)
                self._defer(url, md_capable)

    def _request_kwargs(self, meta):
        """Every page request starts at the adapter's tier (static unless it said otherwise).

        On a re-scrape (previous manifest supplied) the static tier sends the
        stored validators, so an unchanged page answers 304 and is never re-fetched.
        """
        extra = {}
        prev = self.previous.get(meta.get("origin_url", ""))
        # Resume continues the paused frontier — it must NOT turn every frontier
        # fetch into a conditional GET against the prior run (that's update's job).
        if prev and self.start_tier == "static" and not self.resume:
            headers = {}
            if prev.get("etag"):
                headers["If-None-Match"] = prev["etag"]
            if prev.get("last_modified"):
                headers["If-Modified-Since"] = prev["last_modified"]
            if headers:
                extra["headers"] = headers
        if self.start_tier == "static":
            return {"meta": meta, **extra}
        return {"sid": self.start_tier, "meta": {**meta, "tier": self.start_tier}}

    async def on_error(self, request, error):
        """Upstream never retries raised exceptions — do it once, then record the failure."""
        url = request.meta.get("origin_url", clean_url(request.url))
        self._resume_pending.discard(url)  # this run has now touched it
        detail = f"{type(error).__name__}: {error}".replace("\n", " ")[:180]
        if request.meta.get("retry", 0) < 1 and self._engine:
            self.emit(
                {
                    "type": "progress",
                    "url": url,
                    "message": f"🔁 retry after error: {url} ({type(error).__name__})",
                }
            )
            retry = Request(
                request.url,
                sid=request.sid,
                dont_filter=True,
                meta={**request.meta, "retry": 1},
            )
            await self._engine.scheduler.enqueue(retry)
            return
        self.ledger.record(
            url,
            "failed_exception",
            detail,
            tier=request.sid or "static",
            escalated=bool(request.meta.get("escalated")),
            elapsed_ms=self._elapsed(url),
        )
        self.emit({"type": "warning", "url": url, "message": f"❌ failed: {url} ({detail})"})
        await self._top_up()  # this page produced no file — spend its slot

    async def parse(self, response: Response):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.request_cancel()  # backstop: cancel landed before the engine existed
        meta = response.meta
        origin_url = meta.get("origin_url", clean_url(response.url))
        self._resume_pending.discard(origin_url)  # this run has now touched it

        tier = meta.get("tier", "static")
        escalated = bool(meta.get("escalated"))
        depth = meta.get("depth", 0)
        headers = dict(response.headers or {})
        status = response.status

        # 304 FIRST: an unchanged twin on an update run must keep its file, not
        # fall through the markdown gate (which rejects any 304) into a full
        # HTML re-scrape that overwrites the markdown-sourced file.
        if meta.get("md_probe") and not (status == 304 and origin_url in self.previous):
            text = _body_text(response)
            if looks_like_markdown(status, headers, text):
                self._write(
                    origin_url, text.strip(), tier, "llms.txt .md variant", escalated, status
                )
                return
            # Re-request through _request_kwargs so the fallback keeps the
            # adapter's start tier (sid) and the update conditional-GET headers
            # — a bare Request silently downgrades to the static session while
            # meta still claims the higher tier. dont_filter: when the twin url
            # equals the page url, this is a deliberate re-fetch of an
            # already-seen url and the dedup filter must not drop it.
            yield Request(
                origin_url,
                dont_filter=True,
                **self._request_kwargs({**meta, "md_probe": False}),
            )
            return

        if status == 304 and origin_url in self.previous:
            # Unchanged since the previous run: the file on disk stays the truth.
            prev = self.previous[origin_url]
            self.ledger.record(
                origin_url,
                "ok",
                "not modified (304)",
                tier=tier,
                chars=prev.get("chars", 0),
                file=prev.get("file", ""),
                status=304,
                elapsed_ms=self._elapsed(origin_url),
                etag=prev.get("etag"),
                last_modified=prev.get("last_modified"),
            )
            self.writer.names[prev.get("file", "").removesuffix(".md")] = origin_url
            self.emit(
                {
                    "type": "page",
                    "url": origin_url,
                    "file": prev.get("file", ""),
                    "message": f"♻️  unchanged: {prev.get('file', origin_url)}",
                }
            )
            return

        if status == 200:
            validators = (
                header_lookup(headers, "etag") or None,
                header_lookup(headers, "last-modified") or None,
            )
            if any(validators):
                self._remember_validators(origin_url, validators)

        body_text = _body_text(response)
        challenged = is_challenge(status, headers, body_text)
        if status != 200 or challenged:
            reason = "challenge" if challenged else f"http {status}"
            escalation = self._escalate(origin_url, tier, "stealth", depth, reason)
            if escalation:
                yield escalation
            elif challenged and self.interactive and self.browsers and await self._solve(
                origin_url, depth, escalated
            ):
                return  # human solved it; _solve wrote the page
            elif not self._write_best(origin_url):
                self.ledger.record(
                    origin_url,
                    "failed_fetch",
                    reason,
                    tier=tier,
                    status=status,
                    escalated=escalated,
                    elapsed_ms=self._elapsed(origin_url),
                )
                self.emit(
                    {
                        "type": "warning",
                        "url": origin_url,
                        "message": f"⚠️  {reason}: {response.url}",
                    }
                )
                await self._top_up()  # this page produced no file — spend its slot
            return

        if self.adapter is None:
            self._detect_late(origin_url, status, headers, body_text)

        content, how = page_content(body_text, origin_url, self.selectors)
        if len(content) > self.best.get(origin_url, (0,))[0]:
            self.best[origin_url] = (len(content), content, tier, how, escalated, status)

        if len(content) < THIN_CHARS:
            escalation = self._escalate(
                origin_url, tier, "dynamic", depth, f"thin ({len(content)} chars)"
            )
            if escalation:
                yield escalation
                return
            # Tier 3: adapters + heuristics + ladder all failed — one LLM rescue per run.
            if self.adapter is None and not self.rescue_attempted:
                self.rescue_attempted = True
                rescued = rescue_selectors(origin_url, body_text, emit=self.emit)
                if rescued:
                    self.rescued = rescued
                    self.selectors = (rescued["content_selector"],) + tuple(self.selectors)
                    content, how = page_content(body_text, origin_url, self.selectors)
                    if len(content) >= THIN_CHARS:
                        self._write(origin_url, content, tier, f"rescue:{how}", escalated, status)
                        return
            if not self._write_best(origin_url):
                self.ledger.record(
                    origin_url,
                    "skipped_no_content",
                    f"no extractable content ({how})",
                    tier=tier,
                    status=status,
                    escalated=escalated,
                    elapsed_ms=self._elapsed(origin_url),
                )
                self.emit(
                    {
                        "type": "warning",
                        "url": origin_url,
                        "message": f"⚠️  no content: {response.url}",
                    }
                )
                await self._top_up()  # this page produced no file — spend its slot
                return
        else:
            self._write(origin_url, content, tier, how, escalated, status)

        if self.discovery != "crawl":
            return

        for link in response.css("a::attr(href)"):
            href = link.get()
            if bad_href(href):
                continue
            url = clean_url(response.urljoin(href))
            if url in self.seen:
                continue
            reason = self.reject(url) or (
                None if self.robots.allowed(url) else "robots_disallow"
            )
            if reason:
                self.seen.add(url)
                self._exclude(reason)
                continue
            depth = path_depth(url, self.scope)
            self.seen.add(url)
            if self._budget_spent():
                # Held, not dropped. Returning here would throw away every
                # remaining href on this page, and those are exactly the links
                # that would fill the slots this page's dead siblings free.
                self._defer(url, False, depth)
                continue
            self._enqueue(url)
            yield self._page_request(url, False, depth)

    def _detect_late(self, url, status, headers, html):
        """The start sample missed (challenge/non-200): retry detection on each real page.

        Limitation: selectors only apply from the page detection lands on — pages
        already written keep whatever the generic selectors produced.
        """
        adapter = detect_late(url, status, headers, html, emit=self.emit)
        if adapter is None:
            return
        self.adapter = adapter
        self.selectors = selectors_for(adapter, CONTENT_SELECTORS)
        self.emit(
            {
                "type": "platform",
                "platform": adapter.name,
                "url": url,
                "message": f"🧩 platform: {adapter.name} (detected on {url})",
            }
        )

    def _write_best(self, url):
        """Write the richest output any tier produced for this URL."""
        best = self.best.get(url)
        if not best or not best[1]:
            return False
        _, text, tier, how, escalated, status = best
        self._write(url, text, tier, how, escalated, status)
        return True

    def _remember_validators(self, url, validators):
        if self.validators is None:
            self.validators = {}  # lazy so object.__new__ test spiders never share state
        self.validators[url] = validators

    def _write(self, url, text, tier="static", how="", escalated=False, status=None):
        # Update overwrite guard: on a re-scrape, a drastic char-shrink vs the
        # previous manifest keeps the old file and records the page as suspect.
        # The OLD validators are recorded (not this response's), so the next
        # update re-fetches and re-judges instead of 304-ing on suspect content.
        prev = self.previous.get(url) if not self.resume else None
        if (
            prev
            and prev.get("file")
            and prev.get("chars", 0) > 0
            and len(text) < prev["chars"] * UPDATE_SHRINK_RATIO
        ):
            self.ledger.record(
                url,
                "suspect_shrink",
                f"kept previous file: new content {len(text)} chars "
                f"vs {prev['chars']} recorded",
                tier=tier,
                chars=prev["chars"],
                file=prev["file"],
                status=status,
                escalated=escalated,
                elapsed_ms=self._elapsed(url),
                etag=prev.get("etag"),
                last_modified=prev.get("last_modified"),
            )
            self.writer.names[prev["file"].removesuffix(".md")] = url
            self.emit(
                {
                    "type": "warning",
                    "url": url,
                    "message": (
                        f"🛑 suspect shrink ({len(text)} vs {prev['chars']} chars) "
                        f"— kept {prev['file']}"
                    ),
                }
            )
            return
        # Outcome comes from whether this page needed an escalation — never from the tier.
        etag, last_modified = (self.validators or {}).get(url, (None, None))
        self.writer.write(
            url,
            text,
            tier=tier,
            how=how,
            outcome="thin_escalated_ok" if escalated else "ok",
            escalated=escalated,
            status=status,
            elapsed_ms=self._elapsed(url),
            etag=etag,
            last_modified=last_modified,
        )

    async def on_close(self):
        info = {
            "start url": self.start_url,
            "platform": self.adapter.name if self.adapter else "none",
            "scope": self.scope,
            "max depth": self.max_depth,
            "max pages": self.max_pages,
            "discovery": self.discovery,
            "in-scope urls found": len(self.seeds) or "n/a (link crawl)",
            "politeness": (
                f"download_delay={self.download_delay}s, "
                f"concurrent_requests={self.concurrent_requests}, "
                f"per_domain={self.concurrent_requests_per_domain}"
            ),
            "robots policy": ROBOTS_POLICY,
            "browser tiers": "available" if self.browsers else "unavailable (static only)",
        }
        if self.rescued:
            # Candidate permanent adapter: surfaced in the manifest, cached on disk.
            info["rescue"] = self.rescued
        if self._attempt_cap_hit:
            # Why the crawl stopped short, on the receipt: the frontier ran out
            # of attempts, not out of pages worth having.
            info["attempt cap"] = f"{self._attempt_cap} urls tried (reached)"
        cancelled = self.cancel_event is not None and self.cancel_event.is_set()
        if cancelled:
            info["cancelled"] = True
        # Exhausted transient codes (429/503/... past upstream's retries) get their
        # real status; on a cancelled run the never-fetched remainder is "cancelled",
        # not a failure; only truly unexplained drops fall through to reconcile().
        for url in self.ledger.enqueued - set(self.ledger.records):
            status = self.last_status.get(url)
            if status is not None:
                self.ledger.record(
                    url,
                    "failed_fetch",
                    f"http {status} (retries exhausted)",
                    status=status,
                    elapsed_ms=self._elapsed(url),
                )
            elif cancelled:
                self.ledger.record(
                    url,
                    "cancelled",
                    "run cancelled before fetch",
                    elapsed_ms=self._elapsed(url),
                )
        self.manifest = write_run(self.out_dir, self.ledger, info)
        reconciled = self.manifest["reconciled"]
        counts = self.ledger.counts()
        self.emit(
            {
                "type": "progress",
                "counts": counts,
                "excluded": sum(self.ledger.excluded.values()),
                "message": f"\n📊 {counts} | excluded {sum(self.ledger.excluded.values())}",
            }
        )
        self.emit(
            {
                "type": "progress",
                "reconciled": reconciled,
                "message": (
                    f"{'✅' if reconciled else '🔴'} ledger reconciles: "
                    f"{len(self.ledger.records)}/{len(self.ledger.enqueued)}"
                ),
            }
        )
