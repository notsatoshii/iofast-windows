"""Write the immutable raw copy of a capture into Research (RAW)/iofast/.

The raw file preserves the combined one-file text with UNTRUSTED banner,
plus frontmatter with source metadata. Never overwrites an existing file —
appends a date suffix on collision.

Full scrape archival: the entire run directory is copied into the vault
at Research (RAW)/iofast/<raw note stem>/, preserving folder structure.
An index.md is written listing every page as a [[wikilink]]. The raw note
links to this in-vault folder, never to the outside run path.
"""

import json
import re
import shutil
from datetime import date
from pathlib import Path

from ..writer import UNTRUSTED_BANNER

RAW_SUBDIR = "Research (RAW)"
IOFAST_SUBDIR = "iofast"
LOG_FILE = "log.md"
MAX_RAW_CHARS = 200_000  # cap inline content; link to in-vault folder beyond this
MAX_FILE_SIZE = 20 * 1024 * 1024  # skip binaries over 20 MB
SKIP_NAMES = {"README-UNTRUSTED.md", "index.md"}
# Files that are not real content pages (metadata, combined outputs).
NON_CONTENT_NAMES = {"report.md", "index.md"}
# Characters Windows refuses in file and folder names.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _raw_dir(vault: Path) -> Path:
    return vault / RAW_SUBDIR / IOFAST_SUBDIR


def _unique_raw_path(raw_dir: Path, title: str, today: str) -> Path:
    """Find a non-colliding filename. Appends -2, -3, … on conflict."""
    safe_title = _UNSAFE_CHARS.sub("-", title.replace(": ", " - "))
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" .") or "Untitled"
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


def _copy_run_to_vault(run_dir: Path, archive_dir: Path) -> list[Path]:
    """Copy the full run into the vault, preserving structure.

    Skips README-UNTRUSTED.md, dotfiles (Obsidian hides them), the root
    combined file (named after the run dir — raw note already has that text),
    binaries over 20 MB, and anything outside run_dir (symlink escape).
    Returns list of copied .md files (relative to archive_dir) for index.
    """
    run_dir = run_dir.resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    copied_md: list[Path] = []
    # Root combined file is named <run_dir_name>.md — skip it
    combined_root_name = f"{run_dir.name}.md"

    for src in sorted(run_dir.rglob("*")):
        if not src.is_file():
            continue
        # Security: must be inside run_dir
        try:
            rel = src.resolve().relative_to(run_dir)
        except ValueError:
            continue
        # Skip excluded names
        if src.name in SKIP_NAMES:
            continue
        # Skip dotfiles (Obsidian hides them; e.g. .scratch-grab-blog.md)
        if src.name.startswith("."):
            continue
        # Skip root combined file (duplicate of raw note content)
        if rel == Path(combined_root_name):
            continue
        # Skip large binaries
        try:
            if src.stat().st_size > MAX_FILE_SIZE:
                continue
        except OSError:
            continue
        dst = archive_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if src.suffix == ".md":
            copied_md.append(rel)
    return copied_md


def _write_index(
    archive_dir: Path, pages: list[Path], title: str,
    raw_note_stem: str, archive_rel: str,
) -> Path:
    """Write index.md listing every page as a vault-relative [[wikilink|alias]].

    Uses full vault-relative paths so Obsidian resolves unambiguously
    (every capture has a report.md). Links back to the raw note.
    """
    lines = [
        "---",
        "tags: [iofast, index]",
        "---",
        "",
        f"# {title} — full capture index",
        "",
        f"Raw note: [[Research (RAW)/iofast/{raw_note_stem}|{title}]]",
        "",
    ]
    for rel in sorted(pages):
        # Vault-relative path with alias for clean display
        vault_path = f"{archive_rel}/{rel.as_posix()}"
        alias = rel.stem
        lines.append(f"- [[{vault_path}|{alias}]]")
    lines.append("")
    index_path = archive_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def write_raw_copy(
    vault: Path,
    combined_text: str,
    source_url: str,
    source_type: str,
    run_dir: str,
    title: str,
    captured_date: str | None = None,
) -> tuple[Path, dict]:
    """Write the immutable raw copy and archive the full run.

    Returns (raw_note_path, archive_info) where archive_info contains
    page_count, char_count, archive_rel, and truncated flag.
    """
    raw_dir = _raw_dir(vault)
    raw_dir.mkdir(parents=True, exist_ok=True)

    today = captured_date or date.today().isoformat()
    target = _unique_raw_path(raw_dir, title, today)

    # Archive the full run into the vault
    run_path = Path(run_dir).resolve()
    archive_dir = raw_dir / target.stem
    copied_pages = _copy_run_to_vault(run_path, archive_dir)
    archive_rel = archive_dir.relative_to(vault)
    archive_rel_str = str(archive_rel).replace("\\", "/")

    # Count real content pages only (exclude report.md, index.md)
    content_page_count = sum(
        1 for p in copied_pages
        if p.name not in NON_CONTENT_NAMES and not p.name.startswith(".")
    )

    # Write index with back-link to raw note
    _write_index(archive_dir, copied_pages, title, target.stem, archive_rel_str)

    # Cap size: link to in-vault folder, not outside run path
    truncated = len(combined_text) > MAX_RAW_CHARS
    body = combined_text[:MAX_RAW_CHARS] if truncated else combined_text

    # Grammar: "1 page" vs "2 pages"
    page_word = "page" if content_page_count == 1 else "pages"

    lines = [
        "---",
        f"source: \"{source_url}\"",
        f"type: {source_type}",
        f"captured: {today}",
        f"pages: {content_page_count}",
        f"chars: {len(combined_text)}",
        "---",
        "",
        UNTRUSTED_BANNER,
        "",
        f"> Full capture: [[{archive_rel_str}/index|{title} archive]] ({content_page_count} {page_word}, {len(combined_text):,} chars)",
        "",
    ]
    if truncated:
        lines.append(
            f"> **Note:** content truncated at {MAX_RAW_CHARS:,} chars. "
            f"See full capture above."
        )
        lines.append("")
    lines.append(body)

    target.write_text("\n".join(lines), encoding="utf-8")

    archive_info = {
        "page_count": content_page_count,
        "char_count": len(combined_text),
        "archive_rel": str(archive_rel).replace("\\", "/"),
        "truncated": truncated,
    }
    return target, archive_info


def append_log(
    vault: Path,
    title: str,
    source_url: str,
    source_type: str,
    raw_path: Path,
    topic_folder: str,
    captured_date: str | None = None,
    archive_info: dict | None = None,
) -> Path:
    """Append a line to Research (RAW)/iofast/log.md. Returns log path."""
    log_path = _raw_dir(vault) / LOG_FILE
    today = captured_date or date.today().isoformat()
    raw_rel = raw_path.relative_to(vault) if raw_path.is_relative_to(vault) else raw_path

    # Build log line with page count, char count, and folder link
    pages = archive_info.get("page_count", "?") if archive_info else "?"
    chars = archive_info.get("char_count", "?") if archive_info else "?"
    archive_rel = archive_info.get("archive_rel", "") if archive_info else ""
    folder_link = f" · [[{archive_rel}/index]]" if archive_rel else ""

    line = (
        f"- {today} · [[{raw_rel.stem}]] · {source_type} · "
        f"{topic_folder} · pages {pages} · chars {chars}"
        f"{folder_link} · <{source_url}>\n"
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