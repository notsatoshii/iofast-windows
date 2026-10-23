"""Orchestrator: file a capture into the vault in one call.

Reads the run dir, produces combined text, scans the vault, calls the LLM
classifier, then writes all files deterministically. Returns a summary dict.
"""

import json
import re
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
    """Load manifest.json from a run dir. Searches recursively (e.g. web/manifest.json)."""
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        found = list(run_dir.rglob("manifest.json"))
        if found:
            manifest_path = found[0]
        else:
            return None
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


def _extract_entity_description(
    entity: str, combined_text: str, source_title: str, run_dir: Path | None = None,
) -> str:
    """Extract a 1-3 sentence description of an entity from capture text.

    Finds the first mention and extracts surrounding context. Falls back
    to a generic 'mentioned in' line if no useful context found.
    When combined_text is empty, reads .md files from run_dir directly.
    """
    import re as _re

    body = combined_text
    # If combined_text is empty, try reading .md files from run_dir
    if not body.strip() and run_dir is not None:
        parts = []
        for md_file in sorted(run_dir.rglob("*.md")):
            if md_file.name in ("report.md", "README-UNTRUSTED.md"):
                continue
            try:
                parts.append(md_file.read_text(encoding="utf-8"))
            except OSError:
                continue
        body = "\n\n".join(parts)

    # Strip heading lines so they don't pollute descriptions
    text_lines = body.splitlines()
    body_lines = [l for l in text_lines if not l.startswith("# ")]
    body = "\n".join(body_lines)

    # Find first mention (case-insensitive)
    pattern = _re.compile(_re.escape(entity), _re.IGNORECASE)
    match = pattern.search(body)
    if not match:
        return f"Referenced in [[{source_title}]]."

    # Extract surrounding sentences (up to 3)
    start = max(0, match.start() - 200)
    end = min(len(body), match.end() + 300)
    snippet = body[start:end]

    # Split into sentences and take up to 3 that mention the entity
    sentences = _re.split(r'(?<=[.!?])\s+', snippet)
    relevant = []
    for s in sentences:
        if pattern.search(s) and len(s.strip()) > 10:
            clean = s.strip().replace("\n", " ")
            if clean not in relevant:
                relevant.append(clean)
            if len(relevant) >= 3:
                break

    if relevant:
        return " ".join(relevant)
    return f"Referenced in [[{source_title}]]."


def _first_heading_title(run_dir: Path) -> str | None:
    """Return the first H1 heading found in any .md file under run_dir.

    Skips combined files (named after the run dir), reports, and UNTRUSTED
    banners — those have synthetic titles like '<host> documentation'.
    Prefers page-level content files inside subdirectories.
    """
    combined_name = f"{run_dir.name}.md"
    for md_file in sorted(run_dir.rglob("*.md")):
        if md_file.name in ("report.md", "README-UNTRUSTED.md", combined_name):
            continue
        try:
            with md_file.open(encoding="utf-8") as f:
                for line in f:
                    m = _HEADING_RE.match(line.strip())
                    if m:
                        title = m.group(1).strip()
                        # Strip markdown anchor links: [#](#slug) or [text](url)
                        import re as _re
                        title = _re.sub(r"\s*\[.*?\]\(.*?\)", "", title).strip()
                        # Skip UNTRUSTED banner lines and SOURCE lines
                        if title.startswith("---") or "UNTRUSTED" in title:
                            continue
                        return title
        except OSError:
            continue
    return None


def _get_combined_text(run_dir: Path) -> str:
    """Get combined text of ALL pages in page order.

    Priority: run combine (produces proper page-ordered output with
    boilerplate removal) > manifest-ordered pages > sorted .md files.
    Never picks a single page of a multi-page run. Short single-page
    captures (tweet, YouTube watch.md) go in whole.
    """
    # Try to combine — this produces the canonical combined file with
    # pages in site order and boilerplate removed.
    result = combine_site(run_dir)
    if result and result.is_file():
        return result.read_text(encoding="utf-8")

    # No combine output: gather all page .md files in manifest order
    pages: list[str] = []
    seen: set[str] = set()

    # Check each section directory for manifest-ordered pages
    for section in sorted(run_dir.iterdir()):
        if not section.is_dir():
            continue
        manifest_file = section / "manifest.json"
        if manifest_file.is_file():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                for row in manifest.get("pages", []):
                    fname = row.get("file")
                    if fname and row.get("outcome") != "duplicate" and fname not in seen:
                        page_path = section / fname
                        if page_path.is_file():
                            try:
                                pages.append(page_path.read_text(encoding="utf-8"))
                                seen.add(fname)
                            except OSError:
                                continue
            except (json.JSONDecodeError, OSError):
                pass
            # Also grab any .md not listed in manifest
            for md_file in sorted(section.glob("*.md")):
                if md_file.name not in seen and md_file.name not in ("report.md", "README-UNTRUSTED.md"):
                    try:
                        pages.append(md_file.read_text(encoding="utf-8"))
                        seen.add(md_file.name)
                    except OSError:
                        continue

    # Root-level .md files (single-page captures like tweets, YouTube)
    for md_file in sorted(run_dir.glob("*.md")):
        if md_file.name not in seen and md_file.name not in ("report.md", "README-UNTRUSTED.md"):
            try:
                pages.append(md_file.read_text(encoding="utf-8"))
                seen.add(md_file.name)
            except OSError:
                continue

    return "\n\n---\n\n".join(pages) if pages else ""


def match_topic_hubs(
    topics_json_path: Path | None,
    title: str,
    tags: list[str],
    content_prefix: str,
) -> list[dict]:
    """Match a capture to AgentOS topic hubs by keyword overlap.

    Pure function. Returns at most 3 matches sorted by relevance score.
    Each match is {"slug": str, "title": str, "path": str}.
    Returns [] when topics_json_path is None, missing, or unreadable.

    Matching: case-insensitive substring search of each hub's keywords in
    (a) the capture title, (b) its tag list, (c) first ~2000 chars of content.
    Score = number of distinct keywords that hit any of those three fields.
    """
    if topics_json_path is None:
        return []
    try:
        raw = topics_json_path.read_text(encoding="utf-8")
        hubs = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(hubs, list):
        return []

    title_lower = title.lower()
    tags_lower = [t.lower() for t in tags]
    prefix_lower = content_prefix[:2000].lower()

    scored: list[tuple[int, dict]] = []
    for hub in hubs:
        if not isinstance(hub, dict):
            continue
        slug = str(hub.get("slug", "")).strip()
        h_title = str(hub.get("title", "")).strip()
        h_path = str(hub.get("path", "")).strip()
        keywords = hub.get("keywords", [])
        if not isinstance(keywords, list) or not slug or not h_title:
            continue

        hits = 0
        for kw in keywords:
            if not isinstance(kw, str):
                continue
            kw_l = kw.strip().lower()
            if not kw_l:
                continue
            if kw_l in title_lower or any(kw_l in t for t in tags_lower) or kw_l in prefix_lower:
                hits += 1
        if hits > 0:
            scored.append((hits, {"slug": slug, "title": h_title, "path": h_path}))

    scored.sort(key=lambda x: -x[0])
    return [entry for _, entry in scored[:3]]


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
    # Manifest may be at run root or inside a subdirectory (e.g. web/manifest.json)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        found = list(run_dir.rglob("manifest.json"))
        if found:
            manifest_path = found[0]
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

    # Dedupe check: skip if URL or content already filed
    from .dedupe import check_duplicate
    dup = check_duplicate(source_url, vault_path=vault, content_text=combined_text)
    if dup is not None:
        _emit(f"duplicate ({dup['match_type']}): {dup.get('vault_note') or dup.get('run_dir')}")
        return {
            "duplicate": True,
            "match_type": dup["match_type"],
            "existing": dup.get("vault_note") or dup.get("run_dir"),
            "title": title,
            "source_url": source_url,
        }

    # Write raw copy first (no LLM needed) — also archives full run into vault
    _emit("writing raw copy + archiving full scrape")
    raw_path, archive_info = write_raw_copy(
        vault, combined_text, source_url, source_type,
        str(run_dir), title, captured_date=today,
    )
    _emit(f"raw copy: {raw_path.relative_to(vault)} ({archive_info['page_count']} pages, {archive_info['char_count']} chars)")

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

    # Build title/alias sets for entity linking and dedup
    existing_titles = {n["title"] for n in vault_scan.get("notes", [])}
    existing_titles_lower = {t.lower() for t in existing_titles}
    existing_aliases_lower = {a.lower() for a in vault_scan.get("alias_index", [])}

    # Normalize entity names: strip suffixes like Framework/Model/Library/Inc,
    # collapse whitespace, lowercase for comparison. Returns canonical form.
    import re as _re
    _SUFFIX_RE = _re.compile(
        r"\s+(framework|model|library|inc|ltd|corp|llc|platform|system|toolkit|sdk|api|prompting|technique|method|approach|pattern)$",
        _re.IGNORECASE,
    )

    def _normalize_entity(name: str) -> str:
        """Normalize entity name for dedup comparison."""
        n = name.strip()
        n = _SUFFIX_RE.sub("", n)
        n = _re.sub(r"\s+", " ", n).strip()
        return n

    # Build normalized lookup: maps normalized-lower -> (canonical_title, is_existing)
    norm_to_canonical: dict[str, tuple[str, bool]] = {}
    for t in existing_titles:
        norm_to_canonical[_normalize_entity(t).lower()] = (t, True)
    # Aliases resolve to the note that owns them: Obsidian links by filename,
    # so [[alias]] alone would be a dead link.
    for n in vault_scan.get("notes", []):
        for a in n.get("aliases") or []:
            key = _normalize_entity(str(a)).lower()
            if key not in norm_to_canonical:
                norm_to_canonical[key] = (n["title"], True)

    # Track new notes being created this run (normalized -> canonical title)
    new_note_norm_map: dict[str, str] = {}
    new_note_titles = {n["title"] for n in classification.get("new_notes", []) if n.get("title")}

    # Merge near-duplicate new_notes from LLM before processing entities
    merged_new_notes: list[dict] = []
    seen_norms: set[str] = set()
    for note in classification.get("new_notes", []):
        n_title = note.get("title", "").strip()
        if not n_title:
            continue
        norm_key = _normalize_entity(n_title).lower()
        # Check against existing titles/aliases first
        if norm_key in norm_to_canonical:
            # Existing note matches — add alias instead of creating new note
            canonical, _ = norm_to_canonical[norm_key]
            if n_title != canonical:
                # Record alias merge (will be handled by append_to_existing_notes or skipped)
                pass
            continue
        # Check against other new notes in this batch
        if norm_key in seen_norms:
            continue  # duplicate within batch — skip
        seen_norms.add(norm_key)
        new_note_norm_map[norm_key] = n_title
        merged_new_notes.append(note)
    classification["new_notes"] = merged_new_notes

    # Cap entity notes at 8 total (LLM's new_notes + auto-created stubs).
    # Entities are ranked by position in source_note.entities (LLM orders by centrality).
    MAX_ENTITY_NOTES = 8
    current_new_count = len(classification["new_notes"])

    # Process remaining entities from source_note.entities
    # These become wikilinks; only create notes if under cap and not already existing
    entities_list = classification.get("source_note", {}).get("entities", [])
    linked_entities: list[str] = []
    for entity in entities_list:
        e_stripped = str(entity).strip()
        if not e_stripped:
            continue
        norm_key = _normalize_entity(e_stripped).lower()

        # Check if already exists in vault
        if norm_key in norm_to_canonical:
            canonical, _ = norm_to_canonical[norm_key]
            linked_entities.append(canonical)
            continue

        # Check if already being created in this batch
        if norm_key in new_note_norm_map:
            linked_entities.append(new_note_norm_map[norm_key])
            continue

        # Under cap? Create a quality entity note
        if current_new_count < MAX_ENTITY_NOTES:
            # Generate description from capture context (first mention in content)
            desc = _extract_entity_description(e_stripped, combined_text, title, run_dir=run_dir)
            classification["new_notes"].append({
                "title": e_stripped,
                "folder": topic_folder,
                "tags": ["entity"],
                "content": f"{desc}\n\nMentioned in: [[{title}]].\n",
            })
            new_note_norm_map[norm_key] = e_stripped
            new_note_titles.add(e_stripped)
            linked_entities.append(e_stripped)
            current_new_count += 1
        else:
            # Over cap: no note created; the writer renders it as plain text
            linked_entities.append(e_stripped)

    # Update source_note.entities to use canonical names for linking
    classification["source_note"]["entities"] = linked_entities

    # Match AgentOS topic hubs (pure function; no-op when topics.json absent)
    topics_json_path = vault / "AgentOS" / "topics.json"
    topic_hubs = match_topic_hubs(
        topics_json_path, title, classification.get("tags", []), combined_text,
    )
    if topic_hubs:
        hub_titles = [h["title"] for h in topic_hubs]
        _emit(f"topic hubs: {', '.join(hub_titles)}")

    # Write source note
    _emit("writing source note")
    source_note_path = write_source_note(
        vault, topic_folder, title, classification,
        source_url, raw_path, captured_date=today,
        existing_titles=existing_titles,
        new_note_titles=new_note_titles,
        topic_hubs=topic_hubs,
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
    log_path = append_log(vault, title, source_url, source_type, raw_path, topic_folder, captured_date=today, archive_info=archive_info)
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