"""Write imported conversations to markdown files and vault.

Each conversation becomes one markdown file. NO UNTRUSTED banner — these
are Eric's own chats. Raw copies go to Research (RAW)/AI chats/<source>/.
"""

import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path


def _library_dir() -> Path:
    """Library output root for imported chats."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return Path(base) / "iofast" / "library" / "chats"
    return Path.home() / "Library" / "Application Support" / "iofast" / "library" / "chats"


def _safe_filename(title: str, max_len: int = 80) -> str:
    """Sanitize title for use as filename."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', title)
    safe = re.sub(r'\s+', ' ', safe).strip()
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip()
    return safe or "untitled"


def write_conversation(conversation: dict, out_root: Path | None = None,
                       dry_run: bool = False) -> dict:
    """Write one conversation to a markdown file.

    Returns {path, title, message_count, char_count, skipped}.
    No UNTRUSTED banner — these are personal chat exports.
    """
    source = conversation.get("source", "unknown")
    title = conversation.get("title", "Untitled")
    date = conversation.get("date", "")
    messages = conversation.get("messages", [])
    conv_id = conversation.get("id", "unknown")

    if out_root is None:
        out_root = _library_dir()

    # Output path: <out_root>/<source>/<date> <title>.md
    source_dir = out_root / source.capitalize()
    fname = _safe_filename(f"{date} {title}" if date else title)
    out_path = source_dir / f"{fname}.md"

    # Avoid overwrite: append -2, -3 etc.
    if out_path.exists():
        stem = out_path.stem
        suffix = out_path.suffix
        counter = 2
        while out_path.exists():
            out_path = source_dir / f"{stem}-{counter}{suffix}"
            counter += 1

    if dry_run:
        return {
            "path": str(out_path),
            "title": title,
            "message_count": len(messages),
            "char_count": sum(len(m["text"]) for m in messages),
            "dry_run": True,
        }

    source_dir.mkdir(parents=True, exist_ok=True)

    # Build markdown content — no UNTRUSTED banner for personal chats
    lines = []
    lines.append(f"# {title}")
    lines.append("")
    if date:
        lines.append(f"**Date:** {date}")
        lines.append("")
    lines.append(f"**Source:** {source.capitalize()} export")
    lines.append(f"**Messages:** {len(messages)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        speaker = "**You**" if role == "user" else "**Assistant**"
        lines.append(f"### {speaker}")
        lines.append("")
        lines.append(text)
        lines.append("")

    content = "\n".join(lines)
    out_path.write_text(content, encoding="utf-8")

    return {
        "path": str(out_path),
        "title": title,
        "message_count": len(messages),
        "char_count": len(content),
        "dry_run": False,
    }


def load_progress(progress_file: Path) -> set[str]:
    """Load set of completed conversation IDs from progress file."""
    if not progress_file.exists():
        return set()
    try:
        with open(progress_file, encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("done_ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_progress(progress_file: Path, done_ids: set[str]) -> None:
    """Save completed conversation IDs to progress file."""
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    with open(progress_file, "w", encoding="utf-8") as fh:
        json.dump({"done_ids": sorted(done_ids)}, fh)


def import_conversations(conversations: list[dict], source: str,
                         out_root: Path | None = None,
                         dry_run: bool = False,
                         progress_file: Path | None = None,
                         emit=None) -> dict:
    """Import a batch of conversations. Resumable via progress file.

    Returns summary: {written, skipped, errors, total_messages, total_chars, paths}.
    """
    if out_root is None:
        out_root = _library_dir()

    done_ids = load_progress(progress_file) if progress_file else set()
    written = 0
    skipped = 0
    errors = 0
    total_messages = 0
    total_chars = 0
    paths = []

    for conv in conversations:
        conv_id = conv.get("id", "")
        if conv_id in done_ids:
            skipped += 1
            continue

        try:
            result = write_conversation(conv, out_root=out_root, dry_run=dry_run)
            if dry_run:
                total_messages += result["message_count"]
                total_chars += result["char_count"]
                paths.append(result["path"])
            else:
                written += 1
                total_messages += result["message_count"]
                total_chars += result["char_count"]
                paths.append(result["path"])
                done_ids.add(conv_id)

                if emit:
                    emit({
                        "type": "progress",
                        "message": f"wrote: {result['title']} ({result['message_count']} msgs)",
                    })
        except Exception as e:
            errors += 1
            if emit:
                emit({"type": "warning", "message": f"write failed: {e}"})

    # Save progress after successful writes
    if not dry_run and progress_file and done_ids:
        save_progress(progress_file, done_ids)

    return {
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "total_messages": total_messages,
        "total_chars": total_chars,
        "paths": paths,
        "source": source,
    }