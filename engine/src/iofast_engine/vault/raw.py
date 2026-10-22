"""Write the immutable raw copy of a capture into Research (RAW)/iofast/.

The raw file preserves the combined one-file text with UNTRUSTED banner,
plus frontmatter with source metadata. Never overwrites an existing file —
appends a date suffix on collision.
"""

import json
from datetime import date
from pathlib import Path

from ..writer import UNTRUSTED_BANNER

RAW_SUBDIR = "Research (RAW)"
IOFAST_SUBDIR = "iofast"
LOG_FILE = "log.md"
MAX_RAW_CHARS = 200_000  # cap inline content; link to full run beyond this


def _raw_dir(vault: Path) -> Path:
    return vault / RAW_SUBDIR / IOFAST_SUBDIR


def _unique_raw_path(raw_dir: Path, title: str, today: str) -> Path:
    """Find a non-colliding filename. Appends -2, -3, … on conflict."""
    safe_title = title.replace("/", "-").replace("\\", "-").strip()
    base = f"{today} {safe_title}.md"
    candidate = raw_dir / base
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = raw_dir / f"{today} {safe_title}-{suffix}.md"
        if not candidate.exists():
            return candidate
        suffix += 1


def write_raw_copy(
    vault: Path,
    combined_text: str,
    source_url: str,
    source_type: str,
    run_dir: str,
    title: str,
    captured_date: str | None = None,
) -> Path:
    """Write the immutable raw copy. Returns the path written."""
    raw_dir = _raw_dir(vault)
    raw_dir.mkdir(parents=True, exist_ok=True)

    today = captured_date or date.today().isoformat()
    target = _unique_raw_path(raw_dir, title, today)

    # Cap size: keep the full run linked, truncate inline content
    truncated = len(combined_text) > MAX_RAW_CHARS
    body = combined_text[:MAX_RAW_CHARS] if truncated else combined_text

    lines = [
        "---",
        f"source: \"{source_url}\"",
        f"type: {source_type}",
        f"captured: {today}",
        f"iofast_run: \"{run_dir}\"",
        "---",
        "",
        UNTRUSTED_BANNER,
        "",
    ]
    if truncated:
        lines.append(
            f"> **Note:** content truncated at {MAX_RAW_CHARS:,} chars. "
            f"Full capture at `{run_dir}`."
        )
        lines.append("")
    lines.append(body)

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def append_log(
    vault: Path,
    title: str,
    source_url: str,
    source_type: str,
    raw_path: Path,
    topic_folder: str,
    captured_date: str | None = None,
) -> Path:
    """Append a line to Research (RAW)/iofast/log.md. Returns log path."""
    log_path = _raw_dir(vault) / LOG_FILE
    today = captured_date or date.today().isoformat()
    raw_rel = raw_path.relative_to(vault) if raw_path.is_relative_to(vault) else raw_path

    line = (
        f"- {today} · [[{raw_rel.stem}]] · {source_type} · "
        f"{topic_folder} · <{source_url}>\n"
    )

    if not log_path.exists():
        header = [
            "---",
            "tags: [iofast, log]",
            "---",
            "",
            "# iofast capture log",
            "",
        ]
        log_path.write_text("\n".join(header), encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)
    return log_path