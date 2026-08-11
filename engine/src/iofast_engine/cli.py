"""CLI. The only module allowed to write to stdout/stderr."""

import argparse
import json
import sys
from urllib.parse import urlparse

from . import __version__
from .ledger import FAILURE_OUTCOMES

EPILOG = """\
exit codes:
  0  ok
  1  completed with failures (any failed_*/skipped_no_content page, or nothing written)
  2  bad arguments (including --scope that does not prefix the start URL path)
  3  nothing in scope (no URL enqueued after discovery)
"""


def human_emit(event):
    """Pretty one-liners to STDERR — stdout stays a contract."""
    message = event.get("message")
    if message:
        print(message, file=sys.stderr, flush=True)


def ndjson_emit(event):
    """One JSON object per line on STDOUT."""
    print(json.dumps(event, default=str), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="iofast",
        description="Scrape a docs site into markdown.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"iofast-engine {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="crawl a docs site into markdown files")
    scrape.add_argument("url")
    scrape.add_argument("--scope", default=None, help="override the derived scope path prefix")
    scrape.add_argument("--depth", type=int, default=3, help="max path depth beyond the scope")
    scrape.add_argument("--max-pages", type=int, default=200)
    scrape.add_argument("--out", default=None, help="output directory")
    scrape.add_argument("--json", action="store_true", help="NDJSON events on stdout")

    map_p = sub.add_parser("map", help="discovery only: print the section tree, scrape nothing")
    map_p.add_argument("url")
    map_p.add_argument("--scope", default=None, help="override the derived scope path prefix")
    map_p.add_argument("--json", action="store_true", help="map result as JSON on stdout")

    args = ap.parse_args(argv)

    # Argument validation happens before any network call.
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        ap.error("url must be an absolute http(s) URL")
    path = parsed.path or "/"
    if args.scope and not path.startswith(args.scope):
        ap.error(f"--scope {args.scope!r} is not a path prefix of {path!r}")

    emit = ndjson_emit if args.json else human_emit
    # lazy imports: --version and argument errors stay offline
    from .adapters.detection import preflight
    from .adapters.run import harvest_run, known_fail_run

    resolved, adapter = preflight(args.url, emit=emit)

    if args.command == "map":
        from .mapping import map_run, render_tree

        result = map_run(resolved, args.scope, adapter=adapter, emit=emit)
        if args.json:
            print(json.dumps(result), flush=True)
        else:
            print(render_tree(result["tree"], result["scope"]), flush=True)
            print(
                f"\n{result['tree']['total']} pages in scope via {result['discovery']}"
                f" · platform: {result['platform'] or 'none'}",
                file=sys.stderr,
            )
        return 0 if result["pages"] else 3

    if adapter and adapter.known_fail:
        ledger, written, out_dir, manifest = known_fail_run(adapter, resolved, args.out, emit)
    elif adapter and adapter.fetch_mode == "api":
        ledger, written, out_dir, manifest = harvest_run(
            adapter, resolved, args.out, emit, max_pages=args.max_pages
        )
    else:
        from .spider import DocSpider

        spider = DocSpider(
            start_url=resolved,
            max_depth=args.depth,
            max_pages=args.max_pages,
            out_dir=args.out,
            scope=args.scope,  # else derived from the resolved URL, which keeps the user's path
            emit=emit,
            adapter=adapter,
            resolve=False,  # preflight already followed the redirect
        )
        spider.start()
        ledger, written, out_dir, manifest = (
            spider.ledger,
            spider.written,
            spider.out_dir,
            spider.manifest,
        )

    code = exit_code(ledger, written)
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
    return code


def exit_code(ledger, written):
    """0 ok · 1 completed with failures / nothing written · 3 nothing in scope."""
    counts = ledger.counts()
    if not ledger.enqueued:
        return 3
    if sum(counts[outcome] for outcome in FAILURE_OUTCOMES) or not written:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
