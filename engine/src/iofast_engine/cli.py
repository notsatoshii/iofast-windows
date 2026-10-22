"""CLI. The only module allowed to write to stdout/stderr (the RPC server
front end hardens its own stdout ??see rpc.py)."""

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
    """Pretty one-liners to STDERR ??stdout stays a contract."""
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

    section = sub.add_parser("section", help="print the stable <site>/<section> folder for a URL")
    section.add_argument("url")

    combine_p = sub.add_parser(
        "combine", help="join a site folder's pages into one LLM-ready <site>/<site>.md"
    )
    combine_p.add_argument("site_dir")

    grab_p = sub.add_parser("grab", help="grab content from a URL (web/youtube/github)")
    grab_p.add_argument("url")
    grab_p.add_argument("--site", action="store_true", help="crawl entire site (web source only)")
    grab_p.add_argument("--limit", type=int, default=None, help="max items for feeds/profiles")
    grab_p.add_argument("--lang", default=None, help="preferred transcript language (youtube)")
    grab_p.add_argument("--out", required=True, help="output directory")
    grab_p.add_argument("--file", action="store_true", help="file the finished run into the Obsidian vault (opt-in)")
    grab_p.add_argument("--vault", default=None, help="vault path for --file (auto-detected if omitted)")
    grab_p.add_argument("--json", action="store_true", help="NDJSON events on stdout")

    # Vault subcommands (step 6): file, lint, find
    from .cli_vault import add_vault_subcommands
    vault_sub = add_vault_subcommands(sub)

    # Vault extras (step 7): ask, check + top-level digest
    from .cli_vault_extras import add_extras_subcommands
    add_extras_subcommands(vault_sub, sub)

    # Watch subcommands (step 7): add, list, remove, run
    from .cli_watch import add_watch_subcommands
    add_watch_subcommands(sub)

    # Import subcommands (step 7): chatgpt, claude
    from .cli_import import add_import_subcommands
    add_import_subcommands(sub)

    sub.add_parser("rpc", help="JSON-RPC 2.0 sidecar server over stdio (see docs/RPC.md)")

    check_deps_p = sub.add_parser(
        "check-deps", help="verify all source modules and runtime deps are importable"
    )
    check_deps_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

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
    # bundle dir is read-only in a signed .app) ??not just the browsers
    # subcommand: a scrape that skips this never finds installed chromium.
    from .browsers import default_storage_env

    default_storage_env()

    if args.command == "section":
        from .urls import section_folder

        print(section_folder(args.url), flush=True)
        return 0

    if args.command == "combine":
        from .combine import combine

        target = combine(args.site_dir)
        if target is None:
            print(f"no scraped pages under {args.site_dir}", file=sys.stderr)
            return 1
        print(target, flush=True)
        return 0

    if args.command == "vault":
        # Extras commands (ask, check) are dispatched via cli_vault_extras
        if getattr(args, "vault_command", None) in ("ask", "check"):
            from .cli_vault_extras import run_vault_extras
            return run_vault_extras(args)
        from .cli_vault import run_vault
        return run_vault(args)

    if args.command == "digest":
        from .cli_vault_extras import run_digest
        return run_digest(args)

    if args.command == "watch":
        from .cli_watch import run_watch

        return run_watch(args)

    if args.command == "import":
        from .cli_import import run_import

        return run_import(args)

    if args.command == "rpc":
        from .rpc import serve_stdio

        return serve_stdio()

    if args.command == "check-deps":
        # Import every source module and report availability. Used by the
        # app's --self-test to verify the frozen sidecar has all sources.
        from .sources import (
            GitHubSource,
            InstagramSource,
            WebSource,
            XSource,
            YouTubeSource,
        )

        sources = {
            "youtube": YouTubeSource is not None,
            "github": GitHubSource is not None,
            "instagram": InstagramSource is not None,
            "x": XSource is not None,
            "web": WebSource is not None,
        }
        # Also verify key runtime deps are importable.
        deps_ok = True
        dep_errors = []
        for mod_name in ("yt_dlp", "patchright", "scrapling", "trafilatura", "httpx"):
            try:
                __import__(mod_name)
            except ImportError as e:
                deps_ok = False
                dep_errors.append(f"{mod_name}: {e}")

        # Local speech-to-text: faster-whisper + ctranslate2 (CPU).
        transcribe_ok = False
        transcribe_info = ""
        transcribe_error = ""
        try:
            import faster_whisper  # noqa: F401
            import ctranslate2  # noqa: F401
            transcribe_ok = True
            # Check if any whisper model is already downloaded (no download here).
            from .transcribe import _models_root
            models_dir = _models_root()
            if models_dir.is_dir():
                model_dirs = [d for d in models_dir.iterdir() if d.is_dir()]
                if model_dirs:
                    names = ", ".join(sorted(d.name for d in model_dirs))
                    transcribe_info = f"models: {names}"
                else:
                    transcribe_info = "no models downloaded yet"
            else:
                transcribe_info = "models dir not created yet"
        except ImportError as e:
            transcribe_error = str(e)

        # Browser launch test: actually start driver and open/close headless browser.
        browser_ok = False
        browser_info = ""
        browser_error = ""
        try:
            import asyncio
            from .browser_helper import get_async_playwright

            async def _test_browser():
                ap = get_async_playwright()
                pw = await ap().start()
                try:
                    for channel in ("msedge", "chrome"):
                        try:
                            browser = await pw.chromium.launch(
                                channel=channel,
                                headless=True,
                                args=["--headless=new"],
                            )
                            await browser.close()
                            return True, f"system {channel}"
                        except Exception:
                            continue
                    browser = await pw.chromium.launch(headless=True)
                    await browser.close()
                    return True, "managed chromium"
                finally:
                    await pw.stop()

            browser_ok, browser_info = asyncio.run(
                asyncio.wait_for(_test_browser(), timeout=20)
            )
        except Exception as e:
            browser_error = str(e)

        result = {
            "sources": sources,
            "deps_ok": deps_ok,
            "dep_errors": dep_errors,
            "browser_ok": browser_ok,
            "transcribe_ok": transcribe_ok,
        }
        if browser_ok:
            result["browser_info"] = browser_info
        else:
            result["browser_error"] = browser_error
        if transcribe_ok:
            result["transcribe_info"] = transcribe_info
        else:
            result["transcribe_error"] = transcribe_error
        all_available = all(sources.values()) and deps_ok and browser_ok
        if args.json:
            print(json.dumps(result), flush=True)
        else:
            for name, available in sources.items():
                status = "ok" if available else "MISSING"
                print(f"  {name}: {status}", file=sys.stderr)
            if dep_errors:
                for err in dep_errors:
                    print(f"  dep error: {err}", file=sys.stderr)
            if browser_ok:
                print(f"  browser: ok ({browser_info})", file=sys.stderr)
            else:
                print(f"  browser: FAIL ({browser_error})", file=sys.stderr)
            if transcribe_ok:
                print(f"  transcription: ok ({transcribe_info})", file=sys.stderr)
            else:
                print(f"  transcription: FAIL ({transcribe_error})", file=sys.stderr)
            print(
                f"check-deps: {'all sources available' if all_available else 'INCOMPLETE'}",
                file=sys.stderr,
            )
        return 0 if all_available else 1

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
                "drivers only ??chromium missing" if result["drivers"] else "not installed"
            )
            print(f"browser components: {state} ({result['path']})", file=sys.stderr)
        return 0 if result["installed"] else 1

    # Argument validation happens before any network call.
    error = validate(args.url, getattr(args, "scope", None))  # estimate takes no --scope
    if error:
        ap.error(error)

    if args.command == "estimate":
        from .estimate import run_estimate

        result = run_estimate(args.url)
        if args.json:
            print(json.dumps(result), flush=True)
        elif result["source"]:
            print(
                f"{result['page_count']} pages via {result['source']} "
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
                f" 쨌 platform: {result['platform'] or 'none'}",
                file=sys.stderr,
            )
        return 0 if result["pages"] else 3

    if args.command == "grab":
        import asyncio
        from pathlib import Path

        from .sources import GrabOptions, detect

        source = detect(args.url)
        opts = GrabOptions(
            mode="site" if args.site else "page",
            limit=args.limit,
            lang=args.lang,
        )
        out_dir = Path(args.out).resolve()
        result = asyncio.run(source.grab(args.url, out_dir, opts, emit))
        # thin pages count as success (e.g. YouTube without transcript)
        written = result.ok + result.thin
        exit_code = 0 if written > 0 and result.failed == 0 else 1

        # Opt-in filing: --file files the finished run into the vault
        if exit_code == 0 and getattr(args, "file", False):
            from .vault.vault_rpc import vault_file
            try:
                file_result = vault_file(
                    {"run_dir": str(out_dir), "vault": args.vault},
                    emit=emit,
                )
                print(f"filed: {file_result.get('title', '?')} -> {file_result.get('topic_folder', '?')}", file=sys.stderr)
            except Exception as e:
                print(f"vault filing failed: {e}", file=sys.stderr)
                # Don't change exit_code ??grab itself succeeded

        return exit_code

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
