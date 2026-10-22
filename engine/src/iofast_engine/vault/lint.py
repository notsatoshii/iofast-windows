"""Deterministic vault linter (no LLM).

Checks for:
- Orphan notes (no inbound [[links]])
- Broken [[links]] (target doesn't exist)
- Notes missing frontmatter
- Tags used only once
- MOC entries pointing nowhere

--fix only repairs MOC index drift (removes dead entries).
"""

import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_MOC_SUFFIX = " MOC.md"


def lint_vault(vault_path: str | Path, fix: bool = False) -> dict:
    """Lint the vault. Returns report dict. Optionally fixes MOC drift."""
    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise ValueError(f"vault path does not exist: {vault}")

    # Index all notes
    notes = {}  # rel_path -> text
    titles = set()  # all stems (resolvable wikilink targets)
    for md_file in vault.rglob("*.md"):
        if ".obsidian" in str(md_file) or ".trash" in str(md_file):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(md_file.relative_to(vault))
        notes[rel] = text
        titles.add(md_file.stem)

    # Collect all wikilinks and their sources
    all_links = {}  # target_title -> [source_rel_paths]
    missing_frontmatter = []
    tag_usage = {}  # tag -> count

    for rel, text in notes.items():
        if not _FRONTMATTER_RE.match(text):
            missing_frontmatter.append(rel)

        # Extract tags from frontmatter
        fm_match = _FRONTMATTER_RE.match(text)
        if fm_match:
            fm_text = fm_match.group(0)
            for line in fm_text.splitlines():
                if line.strip().startswith("tags:"):
                    inner = line.split(":", 1)[1].strip()
                    if inner.startswith("[") and inner.endswith("]"):
                        for tag in inner[1:-1].split(","):
                            tag = tag.strip().strip('"').strip("'")
                            if tag:
                                tag_usage[tag] = tag_usage.get(tag, 0) + 1

        # Extract wikilinks
        for match in _WIKILINK_RE.finditer(text):
            target = match.group(1).strip()
            if target not in all_links:
                all_links[target] = []
            all_links[target].append(rel)

    # Broken links: target not in titles
    broken_links = []
    for target, sources in all_links.items():
        if target not in titles:
            for source in sources:
                broken_links.append({"source": source, "target": target})

    # Orphans: notes with no inbound links (excluding MOCs and log)
    linked_targets = set(all_links.keys())
    orphans = []
    for rel in notes:
        stem = Path(rel).stem
        if stem.endswith(" MOC") or stem == "log":
            continue
        if stem not in linked_targets:
            orphans.append(rel)

    # Singleton tags
    singleton_tags = [t for t, c in tag_usage.items() if c == 1]

    # MOC drift: MOC entries pointing to nonexistent notes
    moc_drift = []
    for rel, text in notes.items():
        if not rel.endswith(_MOC_SUFFIX):
            continue
        for match in _WIKILINK_RE.finditer(text):
            target = match.group(1).strip()
            if target not in titles:
                moc_drift.append({"moc": rel, "dead_link": target})

    # Fix MOC drift if requested
    fixed_mocs = []
    if fix and moc_drift:
        mocs_to_fix = {}
        for entry in moc_drift:
            moc_rel = entry["moc"]
            if moc_rel not in mocs_to_fix:
                mocs_to_fix[moc_rel] = set()
            mocs_to_fix[moc_rel].add(entry["dead_link"])

        for moc_rel, dead_targets in mocs_to_fix.items():
            moc_path = vault / moc_rel
            text = moc_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            new_lines = []
            for line in lines:
                # Remove lines containing dead wikilinks
                skip = False
                for target in dead_targets:
                    if f"[[{target}]]" in line:
                        skip = True
                        break
                if not skip:
                    new_lines.append(line)
            moc_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            fixed_mocs.append(moc_rel)

    report = {
        "vault_path": str(vault),
        "total_notes": len(notes),
        "broken_links": broken_links,
        "orphans": orphans,
        "missing_frontmatter": missing_frontmatter,
        "singleton_tags": singleton_tags,
        "moc_drift": moc_drift,
        "fixed_mocs": fixed_mocs,
    }
    return report