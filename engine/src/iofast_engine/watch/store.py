"""Watch list persistence. JSON file in platform app-support dir.

Schema per entry:
{
  "id": str (uuid4),
  "url": str,
  "kind": "youtube" | "github" | "web" | "instagram" | "x",
  "every": "daily" | "weekly",
  "file": bool,          # vault-file new items
  "limit": int,          # max new items per run
  "added_at": str (ISO),
  "last_run": str|null (ISO),
  "last_seen": {}        # kind-specific cursor (ids, hashes, timestamps)
}
"""

import json
import os
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _app_support_dir() -> Path:
    """Platform-appropriate config dir. Replace with vault dedupe normalizer
    after merge — this is a local helper until that lands."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.expanduser("~")
        return Path(base) / "iofast"
    # macOS / Linux
    return Path.home() / "Library" / "Application Support" / "iofast"


def _watch_path() -> Path:
    return _app_support_dir() / "watch.json"


def _load() -> list[dict]:
    path = _watch_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save(entries: list[dict]) -> None:
    path = _watch_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, default=str)
    tmp.replace(path)


def _detect_kind(url: str) -> str:
    """Classify URL into a watch kind. Mirrors sources/__init__.py detect()
    but returns a string without importing the full source registry."""
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return "web"
    path = urlparse(url).path.lower()

    # YouTube channel or playlist
    yt_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com"}
    if host in yt_hosts:
        if "/channel/" in path or "/c/" in path or "/@" in path or "/playlist" in path:
            return "youtube"
    if host == "youtu.be":
        return "youtube"

    # GitHub repo
    if host == "github.com" and len(path.strip("/").split("/")) >= 2:
        return "github"

    # Social profiles
    if host in ("instagram.com", "www.instagram.com"):
        return "instagram"
    if host in ("x.com", "twitter.com", "www.x.com", "www.twitter.com"):
        return "x"

    return "web"


def _normalize_url(url: str) -> str:
    """Minimal URL normalization for dedupe. Replace with vault dedupe
    normalizer after merge."""
    url = url.strip()
    if url.endswith("/") and "?" not in url:
        url = url.rstrip("/")
    return url


def add(url: str, every: str = "daily", file: bool = False, limit: int = 10) -> dict:
    """Add a watch entry. Returns the created entry dict."""
    url = _normalize_url(url)
    entries = _load()
    # Dedupe by normalized URL
    for e in entries:
        if _normalize_url(e["url"]) == url:
            return e  # already exists
    entry = {
        "id": uuid.uuid4().hex[:12],
        "url": url,
        "kind": _detect_kind(url),
        "every": every if every in ("daily", "weekly") else "daily",
        "file": bool(file),
        "limit": max(1, min(limit, 50)),
        "added_at": datetime.now(timezone.utc).isoformat(),
        "last_run": None,
        "last_seen": {},
    }
    entries.append(entry)
    _save(entries)
    return entry


def remove(entry_id: str) -> bool:
    """Remove by id prefix. Returns True if found and removed."""
    entries = _load()
    before = len(entries)
    entries = [e for e in entries if not e["id"].startswith(entry_id)]
    if len(entries) < before:
        _save(entries)
        return True
    return False


def list_entries() -> list[dict]:
    return _load()


def update_entry(entry_id: str, **fields) -> dict | None:
    """Update fields on an entry by id prefix. Returns updated entry or None."""
    entries = _load()
    for e in entries:
        if e["id"].startswith(entry_id):
            e.update(fields)
            _save(entries)
            return e
    return None


def get_due_entries(if_due: bool = False) -> list[dict]:
    """Return entries due for a check. If if_due=False, return all."""
    if not if_due:
        return _load()
    now = datetime.now(timezone.utc)
    due = []
    for e in _load():
        lr = e.get("last_run")
        if lr is None:
            due.append(e)
            continue
        try:
            last = datetime.fromisoformat(lr)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            due.append(e)
            continue
        delta = now - last
        if e["every"] == "weekly" and delta.days >= 7:
            due.append(e)
        elif e["every"] == "daily" and delta.days >= 1:
            due.append(e)
    return due