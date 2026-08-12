"""DocSpider — the only module that imports scrapling at module level."""

import os
import threading
import time
from urllib.parse import urlparse

from scrapling.spiders import Request, Response, Spider

from .adapters.detection import detect_late, selectors_for
from .discovery import (
    ROBOTS_POLICY,
    Robots,
    _body_text,
    discover_llms,
    discover_sitemap,
    resolve_start,
    robots_sitemaps,
)
from .extract import CONTENT_SELECTORS, page_content
from .ledger import OUTCOMES, Ledger
from .rescue import rescue_selectors
from .tiers import THIN_CHARS, TIERS, browsers_available, header_lookup, is_challenge
from .urls import (
    bad_href,
    clean_url,
    md_variant,
    norm_host,
    path_depth,
    reject_url,
    scope_prefix,
    suffix_twin,
)
from .writer import Writer, write_run


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
    resume = False  # instances set their own; default keeps fresh runs unchanged
    previous: dict = {}  # read-only fallback; instances get their own in __init__
    validators = None  # lazy: written only through _remember_validators

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
        **kwargs,
    ):
        self.emit = emit or (lambda event: None)
        # Cooperative cancel (RPC): the event is set by another thread; parse()
        # checks it as a backstop for cancels that land before the engine exists.
        self.cancel_event = cancel_event
        self._pause_sent = False
        self._cancel_lock = threading.Lock()
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
                    "message": "⚠️  no Playwright chromium found — static tier only",
                }
            )
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
        """
        from scrapling.fetchers import FetcherSession

        manager.add("static", FetcherSession(timeout=30), default=True)
        if self.browsers:
            from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession

            manager.add(
                "dynamic",
                AsyncDynamicSession(headless=True, disable_resources=True, max_pages=2),
                lazy=True,
            )
            manager.add(
                "stealth",
                AsyncStealthySession(headless=True, solve_cloudflare=True, max_pages=1),
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
        """Idempotent graceful pause, safe from any thread and lifecycle stage.

        pause() raises before the engine exists (RuntimeError) or before
        Spider.__init__ ran (AttributeError); both are swallowed because the
        parse-time backstop retries once the engine is live. _pause_sent is
        only set on success so exactly ONE engine pause is ever sent — a
        second request_pause() escalates to a force-stop mid-write.
        """
        with self._cancel_lock:
            if self._pause_sent:
                return
            try:
                self.pause()
                self._pause_sent = True
            except (RuntimeError, AttributeError):
                pass

    def reject(self, url: str):
        return reject_url(url, self.host, self.scope, self.max_depth, self.keep_locales)

    def _exclude(self, reason):
        self.ledger.exclude(reason)

    def _enqueue(self, url):
        self.ledger.enqueue(url)
        self.started.setdefault(url, time.monotonic())

    def _elapsed(self, url):
        start = self.started.get(url)
        return None if start is None else int((time.monotonic() - start) * 1000)

    def _discover(self):
        scope_root = f"{self.origin}{self.scope.rstrip('/')}"
        # Prefetch robots once for the origin so the seed reconcile never races it.
        self.robots.allowed(f"{self.origin}/")

        pages = discover_sitemap(self.origin, robots_sitemaps(self.origin), emit=self.emit)
        if pages:
            urls = self._reconcile([url for url, _ in pages])
            self.emit(
                {
                    "type": "progress",
                    "message": (
                        f"🗺  sitemap: {len(pages)} urls, {len(urls)} in scope {self.scope}"
                    ),
                }
            )
            if urls:
                return [(url, False) for url in urls], "sitemap"

        llms_url, found = discover_llms(self.origin, scope_root)
        if found:
            urls = self._reconcile(found)
            self.emit(
                {
                    "type": "progress",
                    "message": (
                        f"📄 {llms_url}: {len(found)} urls, {len(urls)} in scope {self.scope}"
                    ),
                }
            )
            if urls:
                return [(url, True) for url in urls], "llms.txt"

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
        seeds = self.seeds[: self.max_pages] if self.seeds else [(self.start_url, False)]
        for url, from_llms in seeds:
            if url in self.seen:
                continue
            self.seen.add(url)
            self._enqueue(url)
            target = md_variant(url) if from_llms else url
            yield Request(target, **self._request_kwargs({"depth": 0, "origin_url": url, "md_probe": from_llms}))

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

    async def parse(self, response: Response):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.request_cancel()  # backstop: cancel landed before the engine existed
        meta = response.meta
        origin_url = meta.get("origin_url", clean_url(response.url))

        tier = meta.get("tier", "static")
        escalated = bool(meta.get("escalated"))
        depth = meta.get("depth", 0)
        headers = dict(response.headers or {})
        status = response.status

        if meta.get("md_probe"):
            ctype = header_lookup(headers, "content-type").lower()
            text = _body_text(response)
            if status == 200 and ("markdown" in ctype or "text/plain" in ctype) and not text.lstrip().startswith("<"):
                self._write(
                    origin_url, text.strip(), tier, "llms.txt .md variant", escalated, status
                )
                return
            yield Request(origin_url, meta={**meta, "md_probe": False})
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
            if len(self.ledger.enqueued) >= self.max_pages:
                return
            self.seen.add(url)
            self._enqueue(url)
            yield Request(
                url, **self._request_kwargs({"depth": path_depth(url, self.scope), "origin_url": url})
            )

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
