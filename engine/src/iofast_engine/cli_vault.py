"""CLI subcommands for vault operations.

Adds `iofast vault file`, `iofast vault lint`, `iofast vault find` to the
main CLI. Imported by cli.py via a small hook — this module is standalone
so other branches' cli.py edits don't conflict.
"""

import argparse
import json
import sys


def add_vault_subcommands(subparsers):
    """Register vault subcommands on the argparse subparsers object.

    Returns the vault sub-subparsers action so extras can add their own
    commands without reaching into argparse internals.
    """
    vault_p = subparsers.add_parser("vault", help="Obsidian vault filing (step 6)")
    vault_sub = vault_p.add_subparsers(dest="vault_command", required=True)

    file_p = vault_sub.add_parser("file", help="file a capture into the vault")
    file_p.add_argument("run_dir", help="path to the iofast run directory")
    file_p.add_argument("--vault", default=None, help="vault path (auto-detected if omitted)")
    file_p.add_argument("--dry-run", action="store_true", help="scan only, no writes")
    file_p.add_argument("--backend", choices=("claude", "openrouter"), default=None)
    file_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    lint_p = vault_sub.add_parser("lint", help="lint the vault for broken links and drift")
    lint_p.add_argument("--vault", default=None, help="vault path (auto-detected if omitted)")
    lint_p.add_argument("--fix", action="store_true", help="fix MOC index drift")
    lint_p.add_argument("--json", action="store_true", help="result as JSON on stdout")

    vault_sub.add_parser("find", help="print detected Obsidian vaults")

    return vault_sub


def run_vault(args):
    """Dispatch vault subcommands. Returns exit code."""
    if args.vault_command == "find":
        from .vault.detect import detect_vaults
        vaults = detect_vaults()
        if not vaults:
            print("no Obsidian vaults found", file=sys.stderr)
            return 1
        for v in vaults:
            status = "open" if v.get("open") else "closed"
            print(f"{v['path']} ({status})", flush=True)
        return 0

    if args.vault_command == "lint":
        from .vault.lint import lint_vault

        vault_path = args.vault
        if vault_path is None:
            from .vault.detect import detect_vaults
            vaults = detect_vaults()
            if not vaults:
                print("no Obsidian vaults found; pass --vault", file=sys.stderr)
                return 1
            open_vaults = [v for v in vaults if v.get("open")]
            vault_path = open_vaults[0]["path"] if open_vaults else vaults[0]["path"]

        report = lint_vault(vault_path, fix=args.fix)
        if args.json:
            print(json.dumps(report, indent=2), flush=True)
        else:
            print(f"vault: {report['vault_path']}", file=sys.stderr)
            print(f"notes: {report['total_notes']}", file=sys.stderr)
            if report["broken_links"]:
                print(f"broken links: {len(report['broken_links'])}", file=sys.stderr)
                for bl in report["broken_links"][:10]:
                    print(f"  {bl['source']} -> [[{bl['target']}]]", file=sys.stderr)
            if report["orphans"]:
                print(f"orphans: {len(report['orphans'])}", file=sys.stderr)
            if report["missing_frontmatter"]:
                print(f"missing frontmatter: {len(report['missing_frontmatter'])}", file=sys.stderr)
            if report["singleton_tags"]:
                print(f"singleton tags: {len(report['singleton_tags'])}", file=sys.stderr)
            if report["moc_drift"]:
                print(f"MOC drift: {len(report['moc_drift'])}", file=sys.stderr)
            if report["fixed_mocs"]:
                print(f"fixed MOCs: {', '.join(report['fixed_mocs'])}", file=sys.stderr)
        return 0

    if args.vault_command == "file":
        from .vault.filer import file_capture

        def emit(event):
            msg = event.get("message")
            if msg:
                print(msg, file=sys.stderr, flush=True)

        try:
            result = file_capture(
                args.run_dir,
                vault_path=args.vault,
                dry_run=args.dry_run,
                backend=args.backend,
                emit=emit,
            )
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(result, indent=2), flush=True)
        else:
            if result.get("duplicate"):
                print(f"duplicate ({result['match_type']}): {result['existing']}", file=sys.stderr)
            elif result.get("dry_run"):
                print(f"dry run: {result['title']} ({result['source_type']}, {result['combined_chars']} chars)", file=sys.stderr)
            else:
                print(f"filed: {result['title']} -> {result['topic_folder']}", file=sys.stderr)
                print(f"raw: {result['raw_path']}", file=sys.stderr)
                print(f"source note: {result['source_note']}", file=sys.stderr)
                if result["new_notes"]:
                    print(f"new notes: {len(result['new_notes'])}", file=sys.stderr)
                if result["modified_notes"]:
                    print(f"modified: {len(result['modified_notes'])}", file=sys.stderr)
        return 0

    print(f"unknown vault command: {args.vault_command}", file=sys.stderr)
    return 1