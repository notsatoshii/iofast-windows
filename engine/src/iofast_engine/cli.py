"""CLI. The only module allowed to write to stdout/stderr (the RPC server
front end hardens its own stdout — see rpc.py)."""

import argparse
import json
import sys

from . import __version__
from .runner import exit_code, run_map, run_scrape, validate  # noqa: F401 (exit_code re-exported)

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
    scrape.add_argument(
        "--update",
        action="store_true",
        help="conditional re-scrape against the previous manifest in --out (304 = keep file)",
    )
    scrape.add_argument(
        "--interactive",
        action="store_true",
        help="on an unsolvable Cloudflare challenge, open a visible browser for a one-time human solve",
    )
    scrape.add_argument("--json", action="store_true", help="NDJSON events on stdout")

    map_p = sub.add_parser("map", help="discovery only: print the section tree, scrape nothing")
    map_p.add_argument("url")
    map_p.add_argument("--scope", default=None, help="override the derived scope path prefix")
    map_p.add_argument("--json", action="store_true", help="map result as JSON on stdout")

    est = sub.add_parser("estimate", help="fast static page-count estimate (hard sub-second budget)")
    est.add_argument("url")
    est.add_argument("--json", action="store_true", help="estimate result as JSON on stdout")

    sub.add_parser("rpc", help="JSON-RPC 2.0 sidecar server over stdio (see docs/RPC.md)")

    browsers_p = sub.add_parser(
        "browsers", help="out-of-band browser components (SHA-256-pinned download)"
    )
    browsers_p.add_argument("action", choices=("status", "install", "verify"))
    browsers_p.add_argument(
        "--repair", action="store_true", help="verify only: reinstall packages that fail"
    )
    browsers_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    args = ap.parse_args(argv)

    # Every command needs browsers/caches rooted in app support (the frozen
    # bundle dir is read-only in a signed .app) — not just the browsers
    # subcommand: a scrape that skips this never finds installed chromium.
    from .browsers import default_storage_env

    default_storage_env()

    if args.command == "rpc":
        from .rpc import serve_stdio

        return serve_stdio()

    if args.command == "browsers":
        from . import browsers

        if args.action == "install":
            try:
                result = browsers.install(emit=human_emit)
            except (browsers.IntegrityError, RuntimeError, OSError) as error:
                print(f"install failed: {error}", file=sys.stderr)
                return 1
        elif args.action == "verify":
            try:
                report = browsers.verify(repair=args.repair, emit=human_emit)
            except (browsers.IntegrityError, RuntimeError, OSError) as error:
                print(f"verify failed: {error}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(report), flush=True)
            else:
                for package, state in report["packages"].items():
                    print(f"{package}: {state}", file=sys.stderr)
                if report.get("repaired"):
                    print(f"repaired: {', '.join(report['repaired'])}", file=sys.stderr)
            return 0 if report["ok"] else 1
        else:
            result = browsers.status()
        if args.json:
            print(json.dumps(result), flush=True)
        else:
            state = "installed" if result["installed"] else (
                "drivers only — chromium missing" if result["drivers"] else "not installed"
            )
            print(f"browser components: {state} ({result['path']})", file=sys.stderr)
        return 0 if result["installed"] else 1

    # Argument validation happens before any network call.
    error = validate(args.url, args.scope)
    if error:
        ap.error(error)

    if args.command == "estimate":
        from .estimate import run_estimate

        result = run_estimate(args.url)
        if args.json:
            print(json.dumps(result), flush=True)
        elif result["source"]:
            print(
                f"≈{result['page_count']} pages via {result['source']} "
                f"in {result['scope']} ({result['elapsed_ms']}ms)",
                file=sys.stderr,
            )
        else:
            print(f"no static estimate available ({result['elapsed_ms']}ms)", file=sys.stderr)
        return 0 if result["source"] else 3

    emit = ndjson_emit if args.json else human_emit

    if args.command == "map":
        from .mapping import render_tree

        result = run_map(args.url, scope=args.scope, emit=emit)
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

    result = run_scrape(
        args.url,
        out=args.out,
        scope=args.scope,
        depth=args.depth,
        max_pages=args.max_pages,
        emit=emit,
        update=args.update,
        interactive=args.interactive,
    )
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
