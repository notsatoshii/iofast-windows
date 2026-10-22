"""CLI subcommands for vault extras: ask, check, digest.

Adds `iofast vault ask`, `iofast vault check`, `iofast digest` to the main CLI.
Imported by cli.py via a small hook — this module is standalone so other
branches' cli.py edits don't conflict.
"""

import argparse
import json
import sys


def add_extras_subcommands(vault_subparsers, top_subparsers):
    """Register extras subcommands. vault_subparsers is the vault sub-subparsers
    object (for ask/check); top_subparsers is the top-level subparsers (for digest)."""
    ask_p = vault_subparsers.add_parser("ask", help="ask a question over your vault notes")
    ask_p.add_argument("question", help="the question to ask")
    ask_p.add_argument("--save", action="store_true", help="save Q&A as a Quicknote")
    ask_p.add_argument("--vault", default=None, help="vault path (auto-detected if omitted)")
    ask_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    check_p = vault_subparsers.add_parser("check", help="weekly vault health check")
    check_p.add_argument("--vault", default=None, help="vault path (auto-detected if omitted)")
    check_p.add_argument("--fix", action="store_true", help="fix MOC drift")
    check_p.add_argument("--week", default=None, help="ISO week label YYYY-Www (default: current)")
    check_p.add_argument("--if-due", action="store_true", help="skip if already run this week")
    check_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    # digest (top-level command, not under vault)
    digest_p = top_subparsers.add_parser("digest", help="weekly research digest")
    digest_p.add_argument("--week", default=None, help="ISO week label YYYY-Www (default: current)")
    digest_p.add_argument("--vault", default=None, help="vault path (auto-detected if omitted)")
    digest_p.add_argument("--if-due", action="store_true", help="skip if already run this week")
    digest_p.add_argument("--json", action="store_true", help="result as JSON on stdout")


def _resolve_vault(vault_arg):
    """Resolve vault path from argument or auto-detect."""
    if vault_arg:
        return vault_arg
    from .vault.detect import detect_vaults
    vaults = detect_vaults()
    if not vaults:
        print("no Obsidian vaults found; pass --vault", file=sys.stderr)
        return None
    open_vaults = [v for v in vaults if v.get("open")]
    return open_vaults[0]["path"] if open_vaults else vaults[0]["path"]


def _check_if_due(job_name: str) -> bool:
    """Check if a weekly job is due based on jobs.json. Returns True if due."""
    import os
    from datetime import date
    jobs_path = os.path.join(os.environ.get("APPDATA", ""), "iofast", "jobs.json")
    if not os.path.isfile(jobs_path):
        return True
    try:
        with open(jobs_path, encoding="utf-8") as f:
            jobs = json.load(f)
    except (json.JSONDecodeError, OSError):
        return True
    last_run = jobs.get(job_name, "")
    if not last_run:
        return True
    try:
        last_date = date.fromisoformat(last_run)
        today = date.today()
        # Due if last run was in a different ISO week
        return last_date.isocalendar()[1] != today.isocalendar()[1] or \
               last_date.isocalendar()[0] != today.isocalendar()[0]
    except ValueError:
        return True


def _record_job_run(job_name: str) -> None:
    """Record that a job ran today in jobs.json."""
    import os
    from datetime import date
    jobs_dir = os.path.join(os.environ.get("APPDATA", ""), "iofast")
    os.makedirs(jobs_dir, exist_ok=True)
    jobs_path = os.path.join(jobs_dir, "jobs.json")
    jobs = {}
    if os.path.isfile(jobs_path):
        try:
            with open(jobs_path, encoding="utf-8") as f:
                jobs = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    jobs[job_name] = date.today().isoformat()
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)


def run_vault_extras(args):
    """Dispatch vault extras subcommands (ask, check). Returns exit code."""
    cmd = getattr(args, "vault_command", None)
    if cmd == "ask":
        return run_vault_ask(args)
    if cmd == "check":
        return run_vault_check(args)
    print(f"unknown vault extras command: {cmd}", file=sys.stderr)
    return 1


def run_vault_ask(args):
    """Run vault ask command. Returns exit code."""
    vault_path = _resolve_vault(args.vault)
    if not vault_path:
        return 1

    from pathlib import Path
    from .vault.ask import ask_vault

    try:
        result = ask_vault(Path(vault_path), args.question, save=args.save)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str), flush=True)
    else:
        print(result.get("answer", ""), flush=True)
        if result.get("sources"):
            print("\nSources:", file=sys.stderr)
            for s in result["sources"]:
                print(f"  - [[{s['title']}]] (score: {s['score']})", file=sys.stderr)
        if result.get("saved_note"):
            print(f"\nSaved: {result['saved_note']}", file=sys.stderr)
    return 0


def run_vault_check(args):
    """Run vault check command. Returns exit code."""
    vault_path = _resolve_vault(args.vault)
    if not vault_path:
        return 1

    if args.if_due and not _check_if_due("vault_check"):
        print("vault check already ran this week; use without --if-due to force", file=sys.stderr)
        return 0

    from pathlib import Path
    from .vault.check import run_check

    try:
        result = run_check(Path(vault_path), fix=args.fix, week=args.week)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.if_due:
        _record_job_run("vault_check")

    if args.json:
        print(json.dumps(result, indent=2, default=str), flush=True)
    else:
        print(f"vault check: {result['week']}", file=sys.stderr)
        print(f"report: {result.get('report_path', '?')}", file=sys.stderr)
        lint = result.get("lint", {})
        print(f"notes: {lint.get('total_notes', 0)}, broken links: {len(lint.get('broken_links', []))}", file=sys.stderr)
        print(f"duplicates: {len(result.get('likely_duplicates', []))}, quicknotes: {result.get('quicknotes_count', 0)}", file=sys.stderr)
        print(f"singleton tags: {len(result.get('singleton_tags', []))}, MOC drift: {len(result.get('moc_drift', []))}", file=sys.stderr)
    return 0


def run_digest(args):
    """Run digest command. Returns exit code."""
    vault_path = _resolve_vault(args.vault)
    if not vault_path:
        return 1

    if args.if_due and not _check_if_due("digest"):
        print("digest already ran this week; use without --if-due to force", file=sys.stderr)
        return 0

    from pathlib import Path
    from .vault.digest import make_digest

    try:
        result = make_digest(Path(vault_path), week=args.week)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.if_due:
        _record_job_run("digest")

    if args.json:
        print(json.dumps(result, indent=2, default=str), flush=True)
    else:
        print(f"digest: {result['week']}", file=sys.stderr)
        print(f"notes: {result['notes_count']}, log entries: {result['log_entries']}", file=sys.stderr)
        print(f"written: {result.get('digest_path', '?')}", file=sys.stderr)
    return 0