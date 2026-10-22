"""Web source: single-page fetch or full-site crawl for any http(s) URL.

This is the universal fallback — every URL that no specialised source claims
lands here. Two modes:

  page  — fetch ONE page through the escalation ladder (static -> dynamic ->
          stealth), extract markdown, write one file with envelope. Works for
          blogs, news articles, any single page that isn't a docs site.

  site  — first check if it's a known docs platform via preflight(). If yes,
          delegate to run_scrape which handles sitemaps, nav parsing, and
          platform-specific extraction. If not, run a generic same-host crawl
          with DocSpider at conservative defaults (max_pages=50, depth=2).

Both paths write through the existing Writer/Ledger/write_run pipeline so
runs look identical to native docs scrapes. Scraped content is untrusted:
every .md file goes through writer.envelope() via Writer.write().
"""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from ..discovery import _body_text, _get
from ..extract import CONTENT_SELECTORS, page_content
from ..ledger import Ledger
from ..tiers import THIN_CHARS, TIERS, browsers_available, is_challenge
from ..writer import Writer, write_run
from .base import GrabOptions, GrabResult


def _is_http(url: str) -> bool:
    """True for any http or https URL. WebSource is the catch-all fallback."""
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


class WebSource:
    """Grab any web page or site as markdown. Fallback source in the registry."""

    name = "web"

    def matches(self, url: str) -> bool:
        return _is_http(url)

    async def grab(
        self, url: str, out_root: Path, opts: GrabOptions, emit
    ) -> GrabResult:
        """Dispatch to page or site mode based on opts.mode."""
        mode = getattr(opts, "mode", "page") or "page"
        if mode == "site":
            return await self._grab_site(url, out_root, opts, emit)
        return await self._grab_page(url, out_root, opts, emit)

    # ------------------------------------------------------------------ page

    async def _grab_page(
        self, url: str, out_root: Path, opts: GrabOptions, emit
    ) -> GrabResult:
        """Fetch one page through the escalation ladder, extract, write."""
        run_dir = out_root / "web"
        run_dir.mkdir(parents=True, exist_ok=True)

        ledger = Ledger()
        writer = Writer(str(run_dir), ledger, max_pages=1, emit=emit)
        ledger.enqueue(url)

        loop = asyncio.get_running_loop()
        text, tier, how, status = await loop.run_in_executor(
            None, self._fetch_with_escalation, url, emit
        )

        if not text:
            ledger.record(
                url, "failed_fetch", f"no extractable content ({how})",
                tier=tier, status=status,
            )
            counts = ledger.counts()
            write_run(str(run_dir), ledger, {"source": "web", "mode": "page", "url": url})
            return GrabResult(
                source="web", run_dir=run_dir,
                ok=counts["ok"], thin=counts.get("thin_escalated_ok", 0), failed=1,
            )

        outcome = "ok"
        if len(text) < THIN_CHARS:
            outcome = "thin_escalated_ok"

        writer.write(url, text, tier=tier, how=how, outcome=outcome, status=status)
        counts = ledger.counts()
        write_run(str(run_dir), ledger, {"source": "web", "mode": "page", "url": url})
        return GrabResult(
            source="web", run_dir=run_dir,
            ok=counts["ok"], thin=counts.get("thin_escalated_ok", 0), failed=0,
        )

    def _fetch_with_escalation(self, url: str, emit) -> tuple[str, str, str, int | None]:
        """Static -> dynamic -> stealth. Returns (markdown, tier, how, status).

        Runs synchronously in an executor thread because scrapling's fetchers
        are sync. The escalation logic mirrors DocSpider's parse() but for a
        single page: try each tier until content clears THIN_CHARS or we run
        out of tiers. Best-tier memory keeps the richest output seen.
        """
        best_text = ""
        best_tier = "static"
        best_how = "none"
        best_status: int | None = None
        has_browsers = browsers_available()

        for tier in TIERS:
            if tier != "static" and not has_browsers:
                break

            response = self._fetch_tier(url, tier)
            if response is None:
                emit({"type": "warning", "url": url, "message": f"⚠️  {tier}: no response"})
                continue

            status = getattr(response, "status", 0)
            headers = dict(getattr(response, "headers", None) or {})
            body = _body_text(response)

            if is_challenge(status, headers, body):
                emit({
                    "type": "progress", "url": url,
                    "message": f"⬆️  challenge on {tier}, escalating: {url}",
                })
                continue

            if status != 200:
                emit({
                    "type": "warning", "url": url,
                    "message": f"⚠️  http {status} on {tier}: {url}",
                })
                # Still try extraction — some 4xx pages carry useful content.
                if status >= 400:
                    continue

            text, how = page_content(body, url, CONTENT_SELECTORS)
            if len(text) > len(best_text):
                best_text = text
                best_tier = tier
                best_how = how
                best_status = status

            if len(text) >= THIN_CHARS:
                return text, tier, how, status

            # Thin content — escalate to next tier if available.
            if tier != "stealth":
                emit({
                    "type": "progress", "url": url,
                    "message": f"⬆️  thin ({len(text)} chars) on {tier}, escalating: {url}",
                })

        return best_text, best_tier, best_how, best_status

    @staticmethod
    def _fetch_tier(url: str, tier: str):
        """One fetch at the given tier. Returns Response or None. Never raises."""
        try:
            if tier == "static":
                from scrapling.fetchers import Fetcher
                return Fetcher.get(url, timeout=30, stealthy_headers=True)
            elif tier == "dynamic":
                from scrapling.fetchers import StealthyFetcher
                return StealthyFetcher.get(
                    url, headless=True, disable_resources=True, timeout=30,
                )
            elif tier == "stealth":
                from scrapling.fetchers import StealthyFetcher
                return StealthyFetcher.get(
                    url, headless=True, solve_cloudflare=True, timeout=30,
                )
        except Exception:
            return None

    # ------------------------------------------------------------------ site

    async def _grab_site(
        self, url: str, out_root: Path, opts: GrabOptions, emit
    ) -> GrabResult:
        """Docs platform -> run_scrape; otherwise -> generic DocSpider crawl."""
        run_dir = out_root / "web"
        run_dir.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()

        # Preflight: resolve + fingerprint to detect docs platforms.
        resolved, adapter = await loop.run_in_executor(
            None, self._preflight, url, emit
        )

        if adapter is not None:
            # Known docs platform — delegate to the full pipeline which handles
            # sitemaps, nav parsing, platform-specific selectors, API harvest.
            result = await loop.run_in_executor(
                None, self._run_docs_scrape, resolved, str(run_dir), emit
            )
        else:
            # Generic site — bounded crawl with conservative defaults.
            result = await loop.run_in_executor(
                None, self._run_generic_crawl, resolved, str(run_dir), emit
            )

        manifest = result.get("manifest") or {}
        counts = manifest.get("counts", {})
        return GrabResult(
            source="web",
            run_dir=run_dir,
            ok=counts.get("ok", 0) + counts.get("thin_escalated_ok", 0),
            thin=counts.get("thin_escalated_ok", 0),
            failed=(
                counts.get("failed_fetch", 0)
                + counts.get("failed_exception", 0)
                + counts.get("skipped_no_content", 0)
                + counts.get("suspect_shrink", 0)
            ),
        )

    @staticmethod
    def _preflight(url, emit):
        """Wrapper around adapters.detection.preflight — never raises."""
        try:
            from ..adapters.detection import preflight
            return preflight(url, emit=emit)
        except Exception as exc:
            emit({"type": "warning", "message": f"⚠️  preflight failed: {exc}"})
            return url, None

    @staticmethod
    def _run_docs_scrape(url, out_dir, emit):
        """Delegate to run_scrape for known docs platforms. Never raises."""
        try:
            from ..runner import run_scrape
            return run_scrape(url, out=out_dir, emit=emit)
        except Exception as exc:
            emit({"type": "warning", "message": f"⚠️  docs scrape failed: {exc}"})
            return {"manifest": {"counts": {}}}

    @staticmethod
    def _run_generic_crawl(url, out_dir, emit):
        """Generic same-host crawl with DocSpider at conservative defaults.

        max_pages=50, depth=2 keeps unattended runs bounded. DocSpider already
        respects robots.txt, applies politeness delays (download_delay=0.5s),
        and writes through Writer/Ledger/write_run — identical output shape to
        a docs-platform scrape.
        """
        try:
            from ..spider import DocSpider

            spider = DocSpider(
                start_url=url,
                max_depth=2,
                max_pages=50,
                out_dir=out_dir,
                emit=emit,
                adapter=None,       # no platform detected — generic selectors
                resolve=False,      # preflight already followed redirects
            )
            spider.start()
            return {
                "written": spider.written,
                "out_dir": spider.out_dir,
                "manifest": spider.manifest,
            }
        except Exception as exc:
            emit({"type": "warning", "message": f"⚠️  generic crawl failed: {exc}"})
            return {"manifest": {"counts": {}}}