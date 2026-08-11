"""Pre-Enter page-count estimate: STATIC-ONLY under a HARD wall-clock budget.

The quick panel's speed-1 pass (docs/RPC.md "estimate"). Two stdlib GETs —
sitemap.xml and llms.txt, raced in parallel threads — and nothing else: never
a browser, never an LLM, never even the scrapling import (which alone can eat
the whole budget in a frozen build). Robots.txt is deliberately skipped: two
tiny well-known-file GETs inside <1s; the real crawl re-checks robots anyway.

The budget is enforced twice: each GET carries it as its socket timeout, and
the caller joins worker threads only until the deadline — a worker that
overruns is abandoned (daemon thread), never awaited.
"""

import threading
import time
import urllib.request
from urllib.parse import urlparse

from .discovery import MAX_BODY, USER_AGENT, parse_llms, parse_sitemap
from .urls import scope_prefix

BUDGET_S = 0.8  # server-side ceiling; the panel gives up at 1s regardless


def _fetch(url: str, timeout: float):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read(MAX_BODY + 1).decode("utf-8", "replace")
    except Exception:
        return None


def _in_scope(urls, origin: str, scope: str) -> int:
    return sum(1 for u in urls if u.startswith(origin) and urlparse(u).path.startswith(scope))


def run_estimate(url: str, budget: float = BUDGET_S) -> dict:
    """{"page_count": int|null, "source": "sitemap"|"llms"|null, "scope", "elapsed_ms"}"""
    deadline = time.monotonic() + budget
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    scope = scope_prefix(url)
    found: dict = {}

    def sitemap_worker():
        text = _fetch(f"{origin}/sitemap.xml", budget)
        if not text:
            return
        pages, nested = parse_sitemap(text)
        if not pages and nested:
            # Index file: follow AT MOST one child, and only with budget left.
            remaining = deadline - time.monotonic()
            if remaining > 0.05:
                child = _fetch(nested[0], remaining)
                if child:
                    pages, _ = parse_sitemap(child)
        if pages:
            found["sitemap"] = _in_scope((u for u, _ in pages), origin, scope)

    def llms_worker():
        text = _fetch(f"{origin}/llms.txt", budget)
        if text:
            urls = parse_llms(text, f"{origin}/")
            if urls:
                found["llms"] = _in_scope(urls, origin, scope)

    started = time.monotonic()
    workers = [
        threading.Thread(target=sitemap_worker, daemon=True),
        threading.Thread(target=llms_worker, daemon=True),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
    # Overrunning workers are abandoned, not awaited: the budget is the contract.

    source = "sitemap" if "sitemap" in found else "llms" if "llms" in found else None
    return {
        "page_count": found.get(source),
        "source": source,
        "scope": scope,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
