"""Per-kind change detection for watch entries.

Each checker function takes (entry, emit) and returns a list of new item dicts.
First run (empty last_seen) records baseline and returns nothing unless
backfill is requested. Checkers are offline-safe: they use yt-dlp flat
listing, GitHub API, RSS/Atom parsing, or page hashing — never a browser.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

# Try httpx; fall back to urllib for environments without it
try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


def _fetch_text(url: str, timeout: float = 20.0) -> str | None:
    """GET url -> text body, or None on failure. No exceptions escape."""
    try:
        if _HAS_HTTPX:
            r = httpx.get(url, follow_redirects=True, timeout=timeout)
            if r.status_code == 200:
                return r.text
        else:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "iofast-watch/1"})
            with urllib.request.urlopen(req, timeout=int(timeout)) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def _fetch_json(url: str, timeout: float = 20.0) -> dict | list | None:
    """GET url -> parsed JSON, or None on failure."""
    text = _fetch_text(url, timeout)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def check_youtube(entry: dict, emit=None) -> list[dict]:
    """Check YouTube channel/playlist for new videos via yt-dlp flat listing."""
    url = entry["url"]
    last_seen = entry.get("last_seen", {})
    seen_ids = set(last_seen.get("video_ids", []))

    try:
        import yt_dlp
        ydl_opts = {
            "flat_playlist": True,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        if emit:
            emit({"type": "warning", "message": f"watch youtube error: {e}"})
        return []

    if not info or "entries" not in info:
        return []

    entries_list = list(info["entries"]) if info["entries"] else []
    current_ids = []
    new_items = []
    limit = entry.get("limit", 10)

    for video in entries_list:
        if video is None:
            continue
        vid = video.get("id")
        if not vid:
            continue
        current_ids.append(vid)
        if vid not in seen_ids:
            new_items.append({
                "id": vid,
                "title": video.get("title", "Untitled"),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "date": video.get("upload_date", ""),
            })

    # First run: record baseline, return nothing (unless backfill)
    if not seen_ids and not last_seen.get("initialized"):
        entry["last_seen"] = {
            "video_ids": current_ids[:100],
            "initialized": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        return []

    # Update seen set (keep last 200 to bound storage)
    merged = list(dict.fromkeys(current_ids + list(seen_ids)))[:200]
    entry["last_seen"] = {
        "video_ids": merged,
        "initialized": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return new_items[:limit]


def check_github(entry: dict, emit=None) -> list[dict]:
    """Check GitHub repo for new releases and README changes."""
    url = entry["url"]
    last_seen = entry.get("last_seen", {})

    # Parse owner/repo from URL
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        return []
    owner, repo = parts[0], parts[1]
    api_base = f"https://api.github.com/repos/{owner}/{repo}"

    new_items = []
    limit = entry.get("limit", 10)

    # Check releases
    releases_data = _fetch_json(f"{api_base}/releases?per_page=10")
    if isinstance(releases_data, list):
        seen_releases = set(last_seen.get("release_tags", []))
        current_tags = []
        for rel in releases_data:
            tag = rel.get("tag_name", "")
            if not tag:
                continue
            current_tags.append(tag)
            if tag not in seen_releases:
                new_items.append({
                    "id": f"release:{tag}",
                    "title": rel.get("name") or tag,
                    "url": rel.get("html_url", url),
                    "date": (rel.get("published_at") or "")[:10],
                    "kind": "release",
                })
        entry.setdefault("last_seen", {})["release_tags"] = current_tags[:50]

    # Check README hash
    readme_data = _fetch_json(f"{api_base}/readme")
    if isinstance(readme_data, dict):
        content_b64 = readme_data.get("content", "")
        sha = readme_data.get("sha", "")
        if sha and sha != last_seen.get("readme_sha"):
            if last_seen.get("readme_sha") is not None:
                new_items.append({
                    "id": f"readme:{sha[:8]}",
                    "title": "README updated",
                    "url": url,
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "kind": "readme",
                })
            entry.setdefault("last_seen", {})["readme_sha"] = sha

    entry.setdefault("last_seen", {})["checked_at"] = datetime.now(timezone.utc).isoformat()
    if not last_seen.get("initialized"):
        entry.setdefault("last_seen", {})["initialized"] = True
        return []  # first run baseline

    return new_items[:limit]


def check_web(entry: dict, emit=None) -> list[dict]:
    """Check website via RSS/Atom feed, sitemap lastmod, or page text hash."""
    url = entry["url"]
    last_seen = entry.get("last_seen", {})
    limit = entry.get("limit", 10)

    # Strategy 1: find RSS/Atom link from page HTML
    html = _fetch_text(url)
    feed_url = None
    if html:
        # Look for <link rel="alternate" type="application/rss+xml" href="...">
        match = re.search(
            r'<link[^>]+type=["\'](?:application/(?:rss|atom)\+xml)["\'][^>]+href=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\'](?:application/(?:rss|atom)\+xml)["\']',
                html, re.IGNORECASE,
            )
        if match:
            feed_url = match.group(1)
            if feed_url.startswith("/"):
                parsed = urlparse(url)
                feed_url = f"{parsed.scheme}://{parsed.netloc}{feed_url}"

    if feed_url:
        return _check_feed(feed_url, last_seen, entry, limit)

    # Strategy 2: sitemap lastmod
    parsed = urlparse(url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    sitemap = _fetch_text(sitemap_url)
    if sitemap:
        return _check_sitemap(sitemap, last_seen, entry, limit)

    # Strategy 3: page text hash
    return _check_hash(url, html, last_seen, entry)


def _check_feed(feed_url: str, last_seen: dict, entry: dict, limit: int) -> list[dict]:
    """Parse RSS/Atom feed for new items."""
    xml_text = _fetch_text(feed_url)
    if not xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    seen_guids = set(last_seen.get("feed_guids", []))
    current_guids = []
    new_items = []

    # Handle both RSS (<item>) and Atom (<entry>)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for item in items:
        guid_el = item.find("guid")
        if guid_el is None:
            guid_el = item.find("atom:id", ns)
        link_el = item.find("link")
        if link_el is None:
            link_el = item.find("atom:link", ns)
        title_el = item.find("title")
        if title_el is None:
            title_el = item.find("atom:title", ns)
        date_el = item.find("pubDate")
        if date_el is None:
            date_el = item.find("atom:updated", ns)
        if date_el is None:
            date_el = item.find("atom:published", ns)

        guid = (guid_el.text if guid_el is not None and guid_el.text else
                link_el.get("href", "") if link_el is not None else "")
        if not guid:
            continue
        current_guids.append(guid)
        if guid not in seen_guids:
            link = ""
            if link_el is not None:
                link = link_el.get("href", "") or (link_el.text or "")
            new_items.append({
                "id": f"feed:{guid[:40]}",
                "title": (title_el.text if title_el is not None and title_el.text else "Untitled"),
                "url": link,
                "date": (date_el.text if date_el is not None and date_el.text else "")[:10],
                "kind": "feed",
            })

    if not seen_guids and not last_seen.get("initialized"):
        entry["last_seen"] = {
            "feed_guids": current_guids[:100],
            "feed_url": feed_url,
            "initialized": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        return []

    merged = list(dict.fromkeys(current_guids + list(seen_guids)))[:200]
    entry["last_seen"] = {
        "feed_guids": merged,
        "feed_url": feed_url,
        "initialized": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return new_items[:limit]


def _check_sitemap(xml_text: str, last_seen: dict, entry: dict, limit: int) -> list[dict]:
    """Check sitemap.xml lastmod dates for changes."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = root.findall(".//sm:url", ns) or root.findall(".//url")

    seen_mods = last_seen.get("sitemap_mods", {})
    current_mods = {}
    new_items = []

    for url_el in urls:
        loc = url_el.find("sm:loc", ns) or url_el.find("loc")
        lastmod = url_el.find("sm:lastmod", ns) or url_el.find("lastmod")
        if loc is None or loc.text is None:
            continue
        loc_text = loc.text.strip()
        mod_text = (lastmod.text if lastmod is not None and lastmod.text else "")
        current_mods[loc_text] = mod_text
        if loc_text in seen_mods and seen_mods[loc_text] != mod_text and mod_text:
            new_items.append({
                "id": f"sitemap:{hashlib.md5(loc_text.encode()).hexdigest()[:8]}",
                "title": f"Updated: {loc_text.split('/')[-1] or loc_text}",
                "url": loc_text,
                "date": mod_text[:10],
                "kind": "sitemap",
            })

    if not seen_mods and not last_seen.get("initialized"):
        entry["last_seen"] = {
            "sitemap_mods": current_mods,
            "initialized": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        return []

    entry["last_seen"] = {
        "sitemap_mods": current_mods,
        "initialized": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return new_items[:limit]


def _check_hash(url: str, html: str | None, last_seen: dict, entry: dict) -> list[dict]:
    """Fall back to hashing the page text."""
    if html is None:
        return []

    # Strip scripts/styles for stable hashing
    clean = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'\s+', ' ', clean).strip()
    h = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]

    prev = last_seen.get("page_hash")
    entry["last_seen"] = {
        "page_hash": h,
        "initialized": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if prev is None:
        return []  # first run baseline
    if h != prev:
        return [{
            "id": f"hash:{h}",
            "title": f"Page changed: {url.split('/')[-1] or url}",
            "url": url,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "kind": "hash",
        }]
    return []


def check_social(entry: dict, emit=None) -> list[dict]:
    """Social profiles require sign-in. Return empty with a warning if not signed in."""
    kind = entry.get("kind", "")
    # Check sign-in status
    try:
        from ..sources.social_rpc import status as social_status
        result = social_status({})
        signed_in = result.get(kind, False)
    except Exception:
        signed_in = False

    if not signed_in:
        if emit:
            emit({
                "type": "warning",
                "message": f"watch: {kind} requires sign-in first (run `iofast browsers` or social.signin)",
            })
        return []

    # If signed in, we could check via the social source — but for now
    # social watching is deferred to a future iteration. Log and skip.
    if emit:
        emit({
            "type": "warning",
            "message": f"watch: {kind} profile watching not yet implemented",
        })
    return []


# Dispatch table
CHECKERS = {
    "youtube": check_youtube,
    "github": check_github,
    "web": check_web,
    "instagram": check_social,
    "x": check_social,
}


def check(entry: dict, emit=None) -> list[dict]:
    """Run the appropriate checker for an entry. Returns list of new items."""
    kind = entry.get("kind", "web")
    checker = CHECKERS.get(kind, check_web)
    try:
        return checker(entry, emit)
    except Exception as e:
        if emit:
            emit({"type": "warning", "message": f"watch check failed ({kind}): {e}"})
        return []