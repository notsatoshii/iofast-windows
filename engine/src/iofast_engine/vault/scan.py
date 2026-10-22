"""Scan an Obsidian vault: folders, notes, tags, MOCs.

Returns a structured snapshot the LLM classifier uses as context.
Never modifies the vault — read-only.
"""

import re
from collections import Counter
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TAG_RE = re.compile(r"#([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_MOC_SUFFIX = " MOC.md"


def _parse_frontmatter(text: str) -> dict:
    """Extract frontmatter fields from a note. Returns empty dict if none."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fm_text = match.group(1)
    result = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Parse YAML-like lists: [a, b, c] or ["a", "b"]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = []
            for item in inner.split(","):
                item = item.strip().strip('"').strip("'")
                if item:
                    items.append(item)
            result[key] = items
        else:
            result[key] = value.strip('"').strip("'")
    return result


def scan_vault(vault_path: str | Path) -> dict:
    """Scan vault structure. Returns dict with folders, notes, tags, mocs."""
    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise ValueError(f"vault path does not exist: {vault}")

    folders = []
    notes = []  # list of {title, aliases, tags, path_rel}
    tag_counts = Counter()
    mocs = []  # list of {folder, title, path_rel}
    all_titles = set()
    all_aliases = set()

    # Top-level folders only (Eric's vault uses flat topic folders)
    for entry in sorted(vault.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            folders.append(entry.name)
            # Scan notes in this folder (non-recursive for speed; Eric's vault
            # is mostly flat per topic)
            for note_path in sorted(entry.glob("*.md")):
                try:
                    text = note_path.read_text(encoding="utf-8")
                except OSError:
                    continue
                fm = _parse_frontmatter(text)
                title = note_path.stem
                aliases = fm.get("aliases", [])
                if isinstance(aliases, str):
                    aliases = [aliases]
                tags = fm.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]

                all_titles.add(title)
                all_aliases.update(aliases)
                tag_counts.update(tags)

                rel = str(note_path.relative_to(vault))
                notes.append({
                    "title": title,
                    "aliases": aliases,
                    "tags": tags,
                    "folder": entry.name,
                    "path": rel,
                })

                if note_path.name.endswith(_MOC_SUFFIX):
                    mocs.append({
                        "folder": entry.name,
                        "title": title,
                        "path": rel,
                    })

    # Also scan root-level .md files
    for note_path in sorted(vault.glob("*.md")):
        if note_path.is_file():
            try:
                text = note_path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = _parse_frontmatter(text)
            title = note_path.stem
            aliases = fm.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [aliases]
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            all_titles.add(title)
            all_aliases.update(aliases)
            tag_counts.update(tags)
            notes.append({
                "title": title,
                "aliases": aliases,
                "tags": tags,
                "folder": "",
                "path": note_path.name,
            })

    return {
        "vault_path": str(vault),
        "folders": folders,
        "notes": notes,
        "tag_vocabulary": dict(tag_counts.most_common()),
        "mocs": mocs,
        "title_index": sorted(all_titles),
        "alias_index": sorted(all_aliases),
    }