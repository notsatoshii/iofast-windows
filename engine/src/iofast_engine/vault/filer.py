"""Orchestrator: file a capture into the vault in one call.

Reads the run dir, produces combined text, scans the vault, calls the LLM
classifier, then writes all files deterministically. Returns a summary dict.
"""

import json
from datetime import date
from pathlib import Path

from ..combine import combine as combine_site
from .classify import classify_capture
from .raw import append_log, write_raw_copy
from .scan import scan_vault
from .writer import (
    append_moc_line,
    append_to_existing_notes,
    write_new_notes,
    write_source_note,
)


def _load_manifest(run_dir: Path) -> dict | None:
    """Load manifest.json from a run dir. Returns None on failure."""
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _detect_source_type(run_dir: Path) -> str:
    """Infer source type from manifest start URL or platform field."""
    manifest = _load_manifest(run_dir)
    if manifest is None:
        return "web"
    info = manifest.get("info", {})
    # Sources use different keys: docs scrapes store 'start_url', multi-source
    # grabs store 'url'. Check both.
    url = str(info.get("url", "") or info.get("start_url", "") or "").lower()
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "github.com" in url:
        return "github"
    if "instagram.com" in url:
        return "instagram"
    if "x.com" in url or "twitter.com" in url:
        return "x"
    platform = info.get("platform")
    if platform:
        return "docs"
    return "web"


def _extract_title(run_dir: Path) -> str:
    """Get title from manifest metadata, content headings, or folder name.

    Priority: manifest info.title > first H1 in combined text > host from URL > folder name.
    Sources store different keys: web/youtube use 'url', docs scrapes use 'start_url'.
    """
    manifest = _load_manifest(run_dir)
    info = manifest.get("info", {}) if manifest else {}

    # 1. Explicit title from source metadata (youtube stores video title here
    #    after the fix; github stores owner/repo).
    explicit = str(info.get("title", "") or "").strip()
    if explicit:
        return explicit

    # 2. First H1 heading from any .md file in the run (combined or page files).
    heading_title = _first_heading_title(run_dir)
    if heading_title:
        return heading_title

    # 3. Host portion of the source URL (try both key names sources use).
    url = str(info.get("url", "") or info.get("start_url", "") or "")
    if url:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if host:
            return host.replace("www.", "")

    # 4. Last resort: folder name.
    return run_dir.name


_HEADING_RE = __import__("re").compile(r"^#\s+(.+)$")


def _first_heading_title(run_dir: Path) -> str | None:
    """Return the first H1 heading found in any .md file under run_dir."""
    for md_file in sorted(run_dir.rglob("*.md")):
        if md_file.name in ("report.md", "README-UNTRUSTED.md"):
            continue
        try:
            with md_file.open(encoding="utf-8") as f:
                for line in f:
                    m = _HEADING_RE.match(line.strip())
                    if m:
                        title = m.group(1).strip()
                        # Skip UNTRUSTED banner lines and SOURCE lines
                        if title.startswith("---") or "UNTRUSTED" in title:
                            continue
                        return title
        except OSError:
            continue
    return None


def _get_combined_text(run_dir: Path) -> str:
    """Get combined text: use existing .md or run combine."""
    # Look for an existing combined file (<site>.md)
    for md_file in run_dir.glob("*.md"):
        if md_file.name not in ("report.md", "README-UNTRUSTED.md"):
            # This might be the combined file
            text = md_file.read_text(encoding="utf-8")
            if len(text) > 500:  # likely the combined file
                return text

    # Try to combine
    result = combine_site(run_dir)
    if result and result.is_file():
        return result.read_text(encoding="utf-8")

    # Fallback: concatenate page files
    pages = []
    for section in sorted(run_dir.iterdir()):
        if section.is_dir() and (section / "manifest.json").is_file():
            for md_file in sorted(section.glob("*.md")):
                if md_file.name not in ("report.md", "README-UNTRUSTED.md"):
                    try:
                        pages.append(md_file.read_text(encoding="utf-8"))
                    except OSError:
                        continue
    return "\n\n---\n\n".join(pages) if pages else ""


def _find_candidate_notes(vault_scan: dict, title: str, tags: list[str]) -> list[dict]:
    """Find existing notes that might relate to this capture."""
    candidates = []
    title_lower = title.lower()
    for note in vault_scan.get("notes", []):
        score = 0
        n_title = note.get("title", "").lower()
        n_aliases = [a.lower() for a in note.get("aliases", [])]
        n_tags = note.get("tags", [])

        # Title overlap
        if title_lower in n_title or n_title in title_lower:
            score += 3
        for alias in n_aliases:
            if title_lower in alias or alias in title_lower:
                score += 2

        # Tag overlap
        for tag in tags:
            if tag in n_tags:
                score += 1

        if score > 0:
            candidates.append((score, note))

    candidates.sort(key=lambda x: -x[0])
    return [note for _, note in candidates[:10]]


def file_capture(
    run_dir: str | Path,
    vault_path: str | Path | None = None,
    dry_run: bool = False,
    backend: str | None = None,
    emit=None,
) -> dict:
    """File a capture into the vault. Returns summary dict."""
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run_dir does not exist: {run_dir}")

    # Detect vault
    if vault_path is None:
        from .detect import detect_vaults
        vaults = detect_vaults()
        if not vaults:
            raise RuntimeError("no Obsidian vaults found")
        # Prefer open vault
        open_vaults = [v for v in vaults if v.get("open")]
        vault_path = open_vaults[0]["path"] if open_vaults else vaults[0]["path"]

    vault = Path(vault_path).resolve()
    if not vault.is_dir():
        raise ValueError(f"vault path does not exist: {vault}")

    def _emit(msg):
        if emit:
            emit({"type": "vault", "message": msg})

    _emit(f"scanning vault at {vault}")
    vault_scan = scan_vault(vault)

    _emit(f"reading capture from {run_dir}")
    source_type = _detect_source_type(run_dir)
    title = _extract_title(run_dir)
    combined_text = _get_combined_text(run_dir)
    source_url = ""
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            info = manifest.get("info", {})
            # Sources use different keys: docs scrapes store 'start url' (with space),
            # multi-source grabs store 'url'. Check both.
            source_url = str(info.get("url", "") or info.get("start url", "") or info.get("start_url", "") or "")
        except (json.JSONDecodeError, OSError):
            pass

    today = date.today().isoformat()

    if dry_run:
        _emit("dry run: skipping LLM classification and file writes")
        return {
            "dry_run": True,
            "vault_path": str(vault),
            "run_dir": str(run_dir),
            "source_type": source_type,
            "title": title,
            "combined_chars": len(combined_text),
        }

    # Write raw copy first (no LLM needed)
    _emit("writing raw copy")
    raw_path = write_raw_copy(
        vault, combined_text, source_url, source_type,
        str(run_dir), title, captured_date=today,
    )
    _emit(f"raw copy: {raw_path.relative_to(vault)}")

    # Find candidate notes for the LLM
    candidates = _find_candidate_notes(vault_scan, title, [])

    # LLM classification
    _emit("classifying with LLM")
    classification = classify_capture(
        content=combined_text,
        source_url=source_url,
        source_type=source_type,
        title=title,
        vault_scan=vault_scan,
        candidate_notes=candidates,
        captured_date=today,
        backend=backend,
    )

    topic_folder = classification["topic_folder"]
    _emit(f"topic: {topic_folder}, tags: {classification['tags']}")

    # Build title sets for entity link filtering (bug fix: only link existing/created notes)
    existing_titles = {n["title"] for n in vault_scan.get("notes", [])}
    new_note_titles = {n["title"] for n in classification.get("new_notes", []) if n.get("title")}

    # Write source note
    _emit("writing source note")
    source_note_path = write_source_note(
        vault, topic_folder, title, classification,
        source_url, raw_path, captured_date=today,
        existing_titles=existing_titles,
        new_note_titles=new_note_titles,
    )
    _emit(f"source note: {source_note_path.relative_to(vault)}")

    # Write new entity/concept notes
    new_note_paths = write_new_notes(vault, classification["new_notes"], captured_date=today)
    for p in new_note_paths:
        _emit(f"new note: {p.relative_to(vault)}")

    # Append to existing notes
    modified_paths = append_to_existing_notes(
        vault, classification["append_sections"], vault_scan,
    )
    for p in modified_paths:
        _emit(f"appended to: {p.relative_to(vault)}")

    # Update MOC
    moc_path = append_moc_line(vault, topic_folder, classification["moc_line"])
    if moc_path:
        _emit(f"MOC updated: {moc_path.relative_to(vault)}")

    # Append log
    log_path = append_log(vault, title, source_url, source_type, raw_path, topic_folder, captured_date=today)
    _emit(f"log updated: {log_path.relative_to(vault)}")

    return {
        "vault_path": str(vault),
        "run_dir": str(run_dir),
        "source_type": source_type,
        "title": title,
        "topic_folder": topic_folder,
        "tags": classification["tags"],
        "raw_path": str(raw_path.relative_to(vault)),
        "source_note": str(source_note_path.relative_to(vault)),
        "new_notes": [str(p.relative_to(vault)) for p in new_note_paths],
        "modified_notes": [str(p.relative_to(vault)) for p in modified_paths],
        "moc_updated": str(moc_path.relative_to(vault)) if moc_path else None,
        "log_path": str(log_path.relative_to(vault)),
    }