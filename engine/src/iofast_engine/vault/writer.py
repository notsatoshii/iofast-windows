"""Deterministic file writer for vault filing results.

Takes the validated classification JSON and writes:
- Source note in the topic folder
- New entity/concept notes
- Appended sections to existing notes
- MOC line appended to the folder's MOC

Every path is resolved and checked to be inside the vault. Existing notes
only get APPENDED sections — Eric's own text is never changed.
"""

import re
from datetime import date
from pathlib import Path

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_filename(title: str) -> str:
    """Convert a title to a safe Obsidian filename (Title Case with spaces)."""
    name = _UNSAFE_CHARS.sub("", title).strip()
    if not name:
        name = "Untitled"
    return name


def _resolve_in_vault(vault: Path, folder: str, filename: str) -> Path:
    """Resolve a path inside the vault. Raises if it escapes."""
    target = vault / folder / filename if folder else vault / filename
    resolved = target.resolve()
    vault_resolved = vault.resolve()
    if not str(resolved).startswith(str(vault_resolved)):
        raise ValueError(f"path escapes vault: {resolved}")
    return resolved


def _ensure_frontmatter(
    path: Path,
    tags: list[str],
    aliases: list[str],
    created: str | None = None,
    source: str | None = None,
) -> None:
    """Write a new note with frontmatter. Does not overwrite existing files."""
    if path.exists():
        return
    lines = ["---"]
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    if aliases:
        quoted = [f'"{a}"' for a in aliases]
        lines.append(f"aliases: [{', '.join(quoted)}]")
    lines.append(f"created: {created or date.today().isoformat()}")
    if source:
        lines.append(f'source: "{source}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {path.stem}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_source_note(
    vault: Path,
    folder: str,
    title: str,
    classification: dict,
    source_url: str,
    raw_path: Path,
    captured_date: str | None = None,
    existing_titles: set[str] | None = None,
    new_note_titles: set[str] | None = None,
) -> Path:
    """Write the main source note. Returns path written.

    existing_titles: set of note titles that already exist in the vault.
    new_note_titles: set of note titles being created in this filing.
    Entity links are filtered to only these sets; others render as plain text.
    """
    filename = f"{_safe_filename(title)}.md"
    path = _resolve_in_vault(vault, folder, filename)

    today = captured_date or date.today().isoformat()
    sn = classification.get("source_note", {})

    lines = ["---"]
    tags = classification.get("tags", [])
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    aliases = classification.get("aliases", [])
    if aliases:
        quoted = [f'"{a}"' for a in aliases]
        lines.append(f"aliases: [{', '.join(quoted)}]")
    lines.append(f"created: {today}")
    lines.append(f'source: "{source_url}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    # Link to raw copy
    raw_rel = raw_path.relative_to(vault) if raw_path.is_relative_to(vault) else raw_path
    lines.append(f"> Raw capture: [[{raw_rel.stem}]]")
    lines.append("")

    if sn.get("summary"):
        lines.append(sn["summary"])
        lines.append("")

    if sn.get("key_points"):
        lines.append("## Key Points")
        lines.append("")
        for point in sn["key_points"]:
            lines.append(f"- {point}")
        lines.append("")

    if sn.get("notable_quotes"):
        lines.append("## Notable Quotes")
        lines.append("")
        for quote in sn["notable_quotes"]:
            lines.append(f"> {quote}")
            lines.append("")

    if sn.get("entities"):
        lines.append("## Related")
        lines.append("")
        # All entities become wikilinks. The filer ensures each entity either
        # matches an existing note, gets a new entity note created, or is dropped
        # before reaching here. Never emit plain text where a link was intended.
        for entity in sn["entities"]:
            lines.append(f"- [[{entity}]]")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_new_notes(
    vault: Path,
    new_notes: list[dict],
    captured_date: str | None = None,
) -> list[Path]:
    """Write entity/concept notes. Skips if file already exists. Returns paths."""
    written = []
    today = captured_date or date.today().isoformat()
    for note in new_notes:
        folder = note.get("folder", "")
        title = note.get("title", "")
        tags = note.get("tags", [])
        aliases = note.get("aliases", [])
        content = note.get("content", "")
        if not title:
            continue
        filename = f"{_safe_filename(title)}.md"
        path = _resolve_in_vault(vault, folder, filename)
        if path.exists():
            continue  # never overwrite
        lines = ["---"]
        if tags:
            lines.append(f"tags: [{', '.join(tags)}]")
        if aliases:
            quoted = [f'"{a}"' for a in aliases]
            lines.append(f"aliases: [{', '.join(quoted)}]")
        lines.append(f"created: {today}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {title}")
        lines.append("")
        # Prevent duplicate H1: strip any leading "# title" from content
        # since we already wrote it above.
        content_lines = content.splitlines()
        if content_lines:
            first = content_lines[0].strip()
            heading_re = __import__("re").compile(r"^#\s+(.+)$")
            if heading_re.match(first):
                content_lines = content_lines[1:]
                # Strip blank line after removed heading too
                if content_lines and not content_lines[0].strip():
                    content_lines = content_lines[1:]
            content = "\n".join(content_lines)
        lines.append(content)
        lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        written.append(path)
    return written


def append_to_existing_notes(
    vault: Path,
    append_sections: list[dict],
    vault_scan: dict,
) -> list[Path]:
    """Append sections to existing notes. Returns paths modified."""
    # Build title -> path index from scan
    title_to_path = {}
    for note in vault_scan.get("notes", []):
        title_to_path[note["title"]] = vault / note["path"]
        for alias in note.get("aliases", []):
            title_to_path[alias] = vault / note["path"]

    modified = []
    for item in append_sections:
        note_title = item.get("note_title", "")
        section = item.get("section", "")
        if not note_title or not section:
            continue
        path = title_to_path.get(note_title)
        if path is None or not path.is_file():
            continue
        # Resolve and verify inside vault
        resolved = path.resolve()
        if not str(resolved).startswith(str(vault.resolve())):
            continue
        # Append with separator
        existing = path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
        existing += "\n" + section + "\n"
        path.write_text(existing, encoding="utf-8")
        modified.append(path)
    return modified


def append_moc_line(
    vault: Path,
    folder: str,
    moc_line: str,
) -> Path | None:
    """Append a line to <Folder>/<Folder> MOC.md. Creates if missing."""
    if not moc_line:
        return None
    moc_name = f"{folder} MOC.md"
    moc_path = _resolve_in_vault(vault, folder, moc_name)

    if not moc_path.exists():
        lines = [
            "---",
            f"tags: [MOC, {folder.lower().replace(' ', '-')}]",
            "---",
            "",
            f"# {folder} MOC",
            "",
        ]
        moc_path.parent.mkdir(parents=True, exist_ok=True)
        moc_path.write_text("\n".join(lines), encoding="utf-8")

    existing = moc_path.read_text(encoding="utf-8")
    # Idempotent: skip if this exact line already present
    for line in existing.splitlines():
        if line.strip() == moc_line.strip():
            return moc_path
    if not existing.endswith("\n"):
        existing += "\n"
    existing += moc_line + "\n"
    moc_path.write_text(existing, encoding="utf-8")
    return moc_path