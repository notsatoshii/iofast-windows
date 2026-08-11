"""The two non-spider run paths: fetch_mode="api" harvest, and known_fail refusal.

Both write the same artefacts a crawl does (report.md, manifest.json, files via
Writer), so a downstream phase cannot tell which path produced a run directory.
"""

import os
from urllib.parse import urlparse

from ..discovery import ROBOTS_POLICY
from ..ledger import Ledger
from ..writer import Writer, write_run
from .base import Ctx


def default_out_dir(url):
    return f"docs-{urlparse(url).netloc.replace('.', '-')}"


def api_get(url, timeout=20, headers=None):
    """Polite static GET (>=0.5s between calls to a host — enforced in discovery._get)."""
    from ..discovery import _get

    return _get(url, timeout=timeout, headers=headers)


def _info(adapter, url, extra):
    return {
        "start url": url,
        "platform": adapter.name,
        "discovery": f"api ({adapter.name})",
        "politeness": "one request at a time, >=0.5s between calls to a host",
        "robots policy": ROBOTS_POLICY,
        **extra,
    }


def harvest_run(adapter, start_url, out_dir=None, emit=None, max_pages=200):
    """Run an adapter's harvest(). The adapter yields Docs; the engine writes them.

    The adapter enqueues every URL before fetching it. Anything still enqueued when
    harvest raises is recorded as failed_exception — never dropped, so a failed run
    can never reconcile green by having enqueued nothing.
    """
    emit = emit or (lambda event: None)
    out_dir = out_dir or default_out_dir(start_url)
    os.makedirs(out_dir, exist_ok=True)
    ledger = Ledger()
    writer = Writer(out_dir, ledger, max_pages=max_pages, emit=emit)
    ctx = Ctx(
        origin=f"{urlparse(start_url).scheme}://{urlparse(start_url).netloc}",
        scope=start_url,
        max_depth=0,
        get=api_get,
        ledger=ledger,
        emit=emit,
    )

    failure = None
    try:
        for doc in adapter.harvest(ctx):
            writer.write(doc.url, doc.markdown, tier="api", how=f"api ({adapter.name})")
            if writer.written >= max_pages:
                break
    except Exception as error:
        failure = f"{type(error).__name__}: {error}".replace("\n", " ")[:180]
        for url in sorted(ledger.enqueued - set(ledger.records)):
            ledger.record(url, "failed_exception", failure, tier="api")
        emit({"type": "warning", "message": f"❌ harvest failed: {failure}"})

    manifest = write_run(out_dir, ledger, _info(adapter, start_url, {"harvest error": failure or "none"}))
    emit(
        {
            "type": "progress",
            "counts": ledger.counts(),
            "reconciled": manifest["reconciled"],
            "message": (
                f"\n📊 {ledger.counts()}\n"
                f"{'✅' if manifest['reconciled'] else '🔴'} ledger reconciles: "
                f"{len(ledger.records)}/{len(ledger.enqueued)}"
            ),
        }
    )
    return ledger, writer.written, out_dir, manifest


def known_fail_run(adapter, start_url, out_dir=None, emit=None):
    """Refuse to scrape a platform we know we cannot scrape. Nothing is fetched."""
    emit = emit or (lambda event: None)
    out_dir = out_dir or default_out_dir(start_url)
    os.makedirs(out_dir, exist_ok=True)
    ledger = Ledger()
    # The start URL is recorded as a failure, so reconcile stays honest about the refusal.
    ledger.record(start_url, "failed_fetch", adapter.known_fail, tier="none")
    emit({"type": "warning", "url": start_url, "message": f"🛑 {adapter.name}: {adapter.known_fail}"})
    manifest = write_run(
        out_dir, ledger, _info(adapter, start_url, {"known fail": adapter.known_fail})
    )
    return ledger, 0, out_dir, manifest
