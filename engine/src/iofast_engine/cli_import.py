"""CLI subcommands for chat history import.

Adds `iofast import chatgpt` and `iofast import claude` to the main CLI.
Imported by cli.py via a small hook — this module is standalone so other
branches' cli.py edits don't conflict.
"""

import json
import sys
from pathlib import Path


def add_import_subcommands(subparsers):
    """Register import subcommands on the argparse subparsers object."""
    import_p = subparsers.add_parser("import", help="import chat history exports")
    import_sub = import_p.add_subparsers(dest="import_command", required=True)

    chatgpt_p = import_sub.add_parser("chatgpt", help="import ChatGPT export archive")
    chatgpt_p.add_argument("path", help="path to export.zip")
    chatgpt_p.add_argument("--dry-run", action="store_true", help="print counts only, no writes")
    chatgpt_p.add_argument("--since", default=None, help="skip conversations before YYYY-MM-DD")
    chatgpt_p.add_argument("--limit", type=int, default=None, help="max conversations to import")
    chatgpt_p.add_argument("--min-messages", type=int, default=4, help="skip conversations with fewer messages")
    chatgpt_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    claude_p = import_sub.add_parser("claude", help="import Claude export archive")
    claude_p.add_argument("path", help="path to export.zip")
    claude_p.add_argument("--dry-run", action="store_true", help="print counts only, no writes")
    claude_p.add_argument("--since", default=None, help="skip conversations before YYYY-MM-DD")
    claude_p.add_argument("--limit", type=int, default=None, help="max conversations to import")
    claude_p.add_argument("--min-messages", type=int, default=4, help="skip conversations with fewer messages")
    claude_p.add_argument("--json", action="store_true", help="result as JSON on stdout")


def run_import(args):
    """Dispatch import subcommands. Returns exit code."""
    zip_path = args.path
    if not Path(zip_path).exists():
        print(f"error: file not found: {zip_path}", file=sys.stderr)
        return 1

    source = args.import_command  # "chatgpt" or "claude"

    def emit(event):
        msg = event.get("message")
        if msg:
            print(msg, file=sys.stderr, flush=True)

    try:
        if source == "chatgpt":
            from .import_chat.chatgpt import parse_chatgpt_export
            conversations = parse_chatgpt_export(
                zip_path,
                since=args.since,
                min_messages=args.min_messages,
                limit=args.limit,
            )
        else:
            from .import_chat.claude import parse_claude_export
            conversations = parse_claude_export(
                zip_path,
                since=args.since,
                min_messages=args.min_messages,
                limit=args.limit,
            )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Dry run: print summary stats
    if args.dry_run:
        total_msgs = sum(c["message_count"] for c in conversations)
        total_chars = sum(c["char_count"] for c in conversations)
        dates = [c["date"] for c in conversations if c.get("date")]
        date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

        # LLM call estimate: ~1 call per conversation for classification
        llm_estimate = len(conversations)

        summary = {
            "source": source,
            "conversations": len(conversations),
            "total_messages": total_msgs,
            "total_chars": total_chars,
            "date_range": date_range,
            "llm_calls_estimate": llm_estimate,
            "dry_run": True,
        }
        if getattr(args, "json", False):
            print(json.dumps(summary, indent=2), flush=True)
        else:
            print(f"dry run: {summary['conversations']} conversations", file=sys.stderr)
            print(f"  messages: {summary['total_messages']}", file=sys.stderr)
            print(f"  chars: {summary['total_chars']}", file=sys.stderr)
            print(f"  date range: {summary['date_range']}", file=sys.stderr)
            print(f"  LLM calls estimate: {summary['llm_calls_estimate']}", file=sys.stderr)
        return 0

    # Actual import
    from .import_chat.writer import import_conversations

    progress_file = Path(zip_path).parent / f".iofast-import-{source}-progress.json"
    result = import_conversations(
        conversations, source=source,
        dry_run=False, progress_file=progress_file, emit=emit,
    )

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), flush=True)
    else:
        print(
            f"imported: {result['written']} conversations "
            f"({result['total_messages']} messages, {result['total_chars']} chars)",
            file=sys.stderr,
        )
        if result["skipped"]:
            print(f"  skipped (already done): {result['skipped']}", file=sys.stderr)
        if result["errors"]:
            print(f"  errors: {result['errors']}", file=sys.stderr)

    return 0 if result["errors"] == 0 else 1