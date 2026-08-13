"""Shared run logic: the CLI and the RPC sidecar are both front ends over this.

Module level stays pure (no scrapling); the fetch stack is imported lazily
inside the run functions, same discipline as cli.py. See docs/RPC.md.
"""

import json
import os
from urllib.parse import urlparse

from .ledger import FAILURE_OUTCOMES, Ledger
from .manifest import build_manifest


def validate(url, scope=None):
    """Argument checks shared by both front ends. Returns an error message or None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "url must be an absolute http(s) URL"
    path = parsed.path or "/"
    # The scope-root page itself is in scope: /docs == /docs/ (clean_url strips
    # the trailing slash, so manifests store the root URL without it — resume,
    # re-scrape, and the scheduler all replay that stored form). Same rule as
    # urls.path_depth.
    if scope and not path.startswith(scope) and path.rstrip("/") != scope.rstrip("/"):
        return f"--scope {scope!r} is not a path prefix of {path!r}"
    return None


def exit_code(ledger, written):
    """0 ok · 1 completed with failures / no corpus produced · 3 nothing in scope.

    "Produced" counts ok records, not raw writes: an update run where every
    page answered 304 rewrites nothing yet the corpus on disk is complete.
    """
    counts = ledger.counts()
    if not ledger.enqueued:
        return 3
    produced = written + counts["ok"] + counts["thin_escalated_ok"]
    if sum(counts[outcome] for outcome in FAILURE_OUTCOMES) or not produced:
        return 1
    return 0


def run_map(url, *, scope=None, emit=None):
    """Discovery-only section tree. Returns the map_run result dict."""
    emit = emit or (lambda event: None)
    from .adapters.detection import preflight
    from .mapping import map_run

    resolved, adapter = preflight(url, emit=emit)
    return map_run(resolved, scope, adapter=adapter, emit=emit)


def previous_pages(out, *, include_fileless=False):
    """The prior run's manifest rows (url -> row).

    Conditional re-scrapes (update) want only file-bearing rows — a page to keep
    on a 304. Resume wants ALL rows, the fileless failures/cancelled included, so
    seeding the resumed ledger preserves them and the manifest still covers the
    WHOLE crawl (a pure link-crawl would otherwise never re-link a prior failure).
    """
    try:
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        return {
            r["url"]: r
            for r in manifest.get("pages", [])
            if include_fileless or r.get("file")
        }
    except Exception:
        return {}


def run_scrape(
    url,
    *,
    out=None,
    scope=None,
    depth=3,
    max_pages=200,
    emit=None,
    cancel=None,
    on_spider=None,
    update=False,
    resume=False,
    interactive=False,
    env=None,
):
    """Run one scrape; returns {written, out_dir, exit_code, cancelled, manifest}.

    cancel: optional threading.Event, checked between phases so a cancel that
    lands before the crawl engine exists still stops the run (see docs/RPC.md).
    on_spider: callback handed the live DocSpider before the crawl starts —
    the RPC server uses it to reach request_cancel().
    update: conditional re-scrape — the previous manifest in `out` supplies
    ETag/Last-Modified validators; unchanged pages 304 and keep their files.
    resume: replay a paused crawl's checkpoint frontier; the prior manifest
    seeds the spider's ledger + seen so the resumed run's manifest still
    reconciles over the WHOLE crawl (docs/RPC.md). Mutually exclusive with update.
    interactive: on a Cloudflare challenge the stealth tier can't auto-solve,
    open a visible browser for a one-time human solve (solve.py). Default off —
    unattended/scheduled runs never block on a human.
    env: request-scoped secrets (Tier 3 rescue keys) applied to os.environ for
    THIS run only and restored after — never argv, never a file (PLAN key rule).
    """
    if env:
        saved = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        try:
            return run_scrape(
                url,
                out=out,
                scope=scope,
                depth=depth,
                max_pages=max_pages,
                emit=emit,
                cancel=cancel,
                on_spider=on_spider,
                update=update,
                resume=resume,
                interactive=interactive,
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    emit = emit or (lambda event: None)

    def is_cancelled():
        return cancel is not None and cancel.is_set()

    if is_cancelled():
        return _cancelled_before_run(url, out, emit)

    from .adapters.detection import preflight
    from .adapters.run import harvest_run, known_fail_run

    resolved, adapter = preflight(url, emit=emit)
    if is_cancelled():
        return _cancelled_before_run(url, out, emit)

    if adapter and adapter.known_fail:
        ledger, written, out_dir, manifest = known_fail_run(adapter, resolved, out, emit)
    elif adapter and adapter.fetch_mode == "api":
        ledger, written, out_dir, manifest = harvest_run(
            adapter, resolved, out, emit, max_pages=max_pages, cancel=cancel
        )
    else:
        from .spider import DocSpider

        spider = DocSpider(
            start_url=resolved,
            max_depth=depth,
            max_pages=max_pages,
            out_dir=out,
            scope=scope,  # else derived from the resolved URL, which keeps the user's path
            emit=emit,
            adapter=adapter,
            resolve=False,  # preflight already followed the redirect
            cancel_event=cancel,
            # previous rows drive update's conditional GETs AND resume's ledger seed
            # — but resume needs the fileless failure/cancelled rows too (else a
            # link-crawl resume silently drops them from the whole-crawl manifest).
            previous=(
                previous_pages(out, include_fileless=True)
                if (resume and out)
                else previous_pages(out)
                if (update and out)
                else None
            ),
            resume=resume,
            interactive=interactive,
        )
        if on_spider is not None:
            on_spider(spider)
        if is_cancelled():
            # Cancelled during construction (discovery): nothing was fetched.
            return _cancelled_before_run(url, out or spider.out_dir, emit)
        spider.start()
        ledger, written, out_dir, manifest = (
            spider.ledger,
            spider.written,
            spider.out_dir,
            spider.manifest,
        )

    return _result(written, out_dir, exit_code(ledger, written), is_cancelled(), manifest, emit)


def _cancelled_before_run(url, out, emit):
    """Cancel landed before anything was enqueued: honest empty manifest, exit 3."""
    manifest = build_manifest({"start url": url, "cancelled": True}, Ledger(), True)
    return _result(0, out or "", 3, True, manifest, emit)


def _result(written, out_dir, code, cancelled, manifest, emit):
    # The terminal result event keeps its CLI shape byte-for-byte; the RPC
    # response (this dict) additionally carries the cancelled flag.
    emit(
        {
            "type": "result",
            "written": written,
            "out_dir": out_dir,
            "exit_code": code,
            "manifest": manifest,
            "message": f"\n{'✅' if code == 0 else '⚠️ '} {written} pages -> {out_dir}",
        }
    )
    return {
        "written": written,
        "out_dir": out_dir,
        "exit_code": code,
        "cancelled": cancelled,
        "manifest": manifest,
    }
