"""CLI subcommands for watch list operations.

Adds `iofast watch add`, `iofast watch list`, `iofast watch remove`,
`iofast watch run` to the main CLI. Imported by cli.py via a small hook —
this module is standalone so other branches' cli.py edits don't conflict.
"""

import json
import sys


def add_watch_subcommands(subparsers):
    """Register watch subcommands on the argparse subparsers object."""
    watch_p = subparsers.add_parser("watch", help="monitor URLs for new content")
    watch_sub = watch_p.add_subparsers(dest="watch_command", required=True)

    add_p = watch_sub.add_parser("add", help="add a URL to the watch list")
    add_p.add_argument("url", help="URL to watch")
    add_p.add_argument("--every", choices=("daily", "weekly"), default="daily")
    add_p.add_argument("--file", action="store_true", help="vault-file new items")
    add_p.add_argument("--limit", type=int, default=10, help="max new items per run")
    add_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    list_p = watch_sub.add_parser("list", help="list all watch entries")
    list_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    rm_p = watch_sub.add_parser("remove", help="remove a watch entry by id")
    rm_p.add_argument("id", help="entry id (prefix match)")
    rm_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    run_p = watch_sub.add_parser("run", help="run due watch checks")
    run_p.add_argument("--if-due", action="store_true", help="only check due entries")
    run_p.add_argument("--backfill", type=int, default=0, help="capture N items on first run")
    run_p.add_argument("--json", action="store_true", help="result as JSON on stdout")


def run_watch(args):
    """Dispatch watch subcommands. Returns exit code."""
    if args.watch_command == "add":
        from .watch.store import add
        entry = add(args.url, every=args.every, file=args.file, limit=args.limit)
        if getattr(args, "json", False):
            print(json.dumps({"added": True, "entry": entry}, indent=2), flush=True)
        else:
            print(f"added: {entry['kind']} {entry['url']} (id={entry['id']})", file=sys.stderr)
        return 0

    if args.watch_command == "list":
        from .watch.store import list_entries
        entries = list_entries()
        if getattr(args, "json", False):
            print(json.dumps({"entries": entries, "count": len(entries)}, indent=2), flush=True)
        else:
            if not entries:
                print("no watch entries", file=sys.stderr)
            for e in entries:
                last = e.get("last_run", "never")
                if last and len(last) > 10:
                    last = last[:10]
                print(
                    f"{e['id']}  {e['kind']:8s}  {e['every']:6s}  "
                    f"last={last}  {e['url']}",
                    flush=True,
                )
        return 0

    if args.watch_command == "remove":
        from .watch.store import remove
        found = remove(args.id)
        if getattr(args, "json", False):
            print(json.dumps({"removed": found, "id": args.id}, indent=2), flush=True)
        else:
            if found:
                print(f"removed: {args.id}", file=sys.stderr)
            else:
                print(f"not found: {args.id}", file=sys.stderr)
        return 0 if found else 1

    if args.watch_command == "run":
        from .watch.runner import run_watch as do_run

        def emit(event):
            msg = event.get("message")
            if msg:
                print(msg, file=sys.stderr, flush=True)

        result = do_run(if_due=args.if_due, backfill=args.backfill, emit=emit)
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, default=str), flush=True)
        else:
            print(
                f"checked: {result['checked']}, new: {result['new_items']}, "
                f"errors: {result['errors']}",
                file=sys.stderr,
            )
        return 0 if result["errors"] == 0 else 1

    print(f"unknown watch command: {args.watch_command}", file=sys.stderr)
    return 1