"""URL normalization and duplicate detection for vault filing.

Normalizes URLs across platforms (YouTube, X, Instagram) so the same content
under different URL forms is recognized as a duplicate. Checks both the iofast
library (run manifests) and vault raw notes' source: frontmatter.
"""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Drop tracking params (utm_*, fbclid, gclid, ref, source)
    - youtu.be/<id>, /shorts/<id>, m.youtube.com -> youtube.com/watch?v=<id>
    - twitter.com -> x.com
    - instagram /reel/<id> and /p/<id> -> instagram.com/p/<id>
    - Strip trailing slashes, lowercase host
    """
    if not url or not url.strip():
        return ""
    url = url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")

    # YouTube normalization
    if host in ("youtu.be", "www.youtu.be"):
        vid = path.lstrip("/")
        if vid:
            return f"https://youtube.com/watch?v={vid}"
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if path.startswith("/shorts/"):
            vid = path.split("/shorts/")[1].split("/")[0]
            return f"https://youtube.com/watch?v={vid}"
        if path.startswith("/watch"):
            qs = parse_qs(parsed.query)
            vid = qs.get("v", [""])[0]
            if vid:
                return f"https://youtube.com/watch?v={vid}"

    # X/Twitter normalization
    if host in ("twitter.com", "www.twitter.com"):
        host = "x.com"

    # Instagram normalization: /reel/<id> -> /p/<id>
    if host in ("instagram.com", "www.instagram.com"):
        reel_match = re.match(r"^/reel/([A-Za-z0-9_-]+)", path)
        if reel_match:
            path = f"/p/{reel_match.group(1)}"
        p_match = re.match(r"^/p/([A-Za-z0-9_-]+)", path)
        if p_match:
            return f"https://instagram.com/p/{p_match.group(1)}"

    # Drop tracking query params
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "fbclid", "gclid", "ref", "source", "si", "feature"}
    qs = parse_qs(parsed.query, keep_blank_values=True)
    clean_qs = {k: v for k, v in qs.items() if k.lower() not in tracking}
    clean_query = urlencode(clean_qs, doseq=True)

    return urlunparse((
        parsed.scheme or "https",
        host,
        path,
        parsed.params,
        clean_query,
        "",  # drop fragment
    ))


def content_hash(text: str) -> str:
    """SHA-256 of normalized text for content-based dedup."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _scan_vault_sources(vault_path: Path) -> dict[str, Path]:
    """Build normalized_url -> raw_note_path index from vault frontmatter."""
    index = {}
    raw_dir = vault_path / "Research (RAW)" / "iofast"
    if not raw_dir.is_dir():
        return index
    for md_file in raw_dir.glob("*.md"):
        if md_file.name == "log.md":
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            # Extract source: from YAML frontmatter
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    fm = text[3:end]
                    for line in fm.split("\n"):
                        if line.strip().startswith("source:"):
                            url = line.split(":", 1)[1].strip().strip('"').strip("'")
                            norm = normalize_url(url)
                            if norm:
                                index[norm] = md_file
                            break
        except OSError:
            continue
    return index


def _scan_library_manifests(library_root: Path) -> dict[str, Path]:
    """Build normalized_url -> run_dir index from iofast library manifests."""
    index = {}
    if not library_root.is_dir():
        return index
    for manifest in library_root.rglob("manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            info = data.get("info", {})
            url = str(info.get("url", "") or info.get("start_url", "") or "")
            norm = normalize_url(url)
            if norm:
                index[norm] = manifest.parent
        except (json.JSONDecodeError, OSError):
            continue
    return index


def check_duplicate(
    url: str,
    vault_path: Path | None = None,
    library_root: Path | None = None,
    content_text: str | None = None,
) -> dict | None:
    """Check if a URL or content already exists. Returns duplicate info or None.

    Returns: {"run_dir": str, "vault_note": str|None, "match_type": "url"|"content"}
    """
    norm = normalize_url(url)

    # Check vault raw notes
    if vault_path:
        vault_index = _scan_vault_sources(vault_path)
        if norm in vault_index:
            return {
                "run_dir": None,
                "vault_note": str(vault_index[norm]),
                "match_type": "url",
            }

    # Check library manifests
    if library_root:
        lib_index = _scan_library_manifests(library_root)
        if norm in lib_index:
            return {
                "run_dir": str(lib_index[norm]),
                "vault_note": None,
                "match_type": "url",
            }

    # Content hash dedup (same text under different URL)
    if content_text and vault_path:
        ch = content_hash(content_text)
        raw_dir = vault_path / "Research (RAW)" / "iofast"
        if raw_dir.is_dir():
            for md_file in raw_dir.glob("*.md"):
                if md_file.name == "log.md":
                    continue
                try:
                    text = md_file.read_text(encoding="utf-8")
                    if content_hash(text) == ch:
                        return {
                            "run_dir": None,
                            "vault_note": str(md_file),
                            "match_type": "content",
                        }
                except OSError:
                    continue

    return None


def normalize_title(title: str) -> str:
    """Normalize a note title for entity dedup matching.

    Lowercase, strip ALL whitespace and punctuation so 'Open AI' matches
    'OpenAI', 'Deep-Mind' matches 'Deep Mind', etc. Handles simple plurals.
    """
    t = title.lower().strip()
    # Remove all non-word characters (including spaces, hyphens, punctuation)
    t = re.sub(r"[^\w]", "", t)
    # Remove trailing 's' for simple plural matching
    if t.endswith("s") and len(t) > 2:
        t_singular = t[:-1]
        # Only strip if it doesn't create an empty or single-char string
        if len(t_singular) > 1:
            return t_singular
    return t


def find_existing_entity(title: str, existing_titles: set[str]) -> str | None:
    """Find an existing note title that matches the given entity name.

    Returns the existing title if found, None otherwise.
    """
    norm = normalize_title(title)
    for existing in existing_titles:
        if normalize_title(existing) == norm:
            return existing
    return None