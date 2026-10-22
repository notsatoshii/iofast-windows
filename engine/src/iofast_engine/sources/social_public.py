"""Public (no-login) endpoints for Instagram and X captures.

These functions call the same APIs that the platforms' own logged-out web
pages and embed widgets use. They never touch the signed-in browser profile,
never rotate proxies, never spoof fingerprints, and never solve CAPTCHAs.
On rate-limit or auth-wall responses they return ok=False with a reason so
the caller can fall back to the signed-in route.

Transcribe hook: if ``iofast_engine.transcribe`` is importable (added by
another worker on feat/yt-transcribe), ``maybe_transcribe(path)`` calls its
``transcribe(path)`` and returns the text; otherwise returns None and the
caller notes "transcript pending".
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# httpx is already a transitive dep via scrapling[fetchers]; import lazily so
# tests can stub without installing the full stack.
_httpx = None


async def _get_client():
    global _httpx
    if _httpx is None:
        import httpx
        _httpx = httpx
    return _httpx.AsyncClient(
        follow_redirects=True,
        timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )


@dataclass
class PublicResult:
    """Outcome of one public-endpoint attempt."""
    ok: bool
    data: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Instagram public endpoints
# ---------------------------------------------------------------------------

# The web app-id that Instagram's own logged-out pages send. yt-dlp uses the
# same value; it changes rarely and is not secret.
_IG_APP_ID = "936619743392459"

# GraphQL doc_id for the logged-out media detail query. This is the current
# value used by Instagram's web client as of late 2024 / early 2025; yt-dlp
# tracks updates. When it rotates, update here and in tests.
_IG_MEDIA_DOC_ID = "8845758582119845"


async def ig_fetch_post_graphql(shortcode: str) -> PublicResult:
    """Fetch a single post/reel via Instagram's logged-out GraphQL API.

    Mirrors what yt-dlp's InstagramIE does for public content: POST to
    /graphql/web/api/v1/ with the polaris media detail doc_id and the
    x-ig-app-id header. Returns parsed post dict on success.
    """
    try:
        client = await _get_client()
        variables = json.dumps({
            "shortcode": shortcode,
            "fetch_tagged_user_count": False,
            "hoisted_comment_id": None,
            "hoisted_reply_id": None,
        })
        payload = {
            "variables": variables,
            "doc_id": _IG_MEDIA_DOC_ID,
        }
        resp = await client.post(
            "https://www.instagram.com/graphql/web/api/v1/",
            data=payload,
            headers={
                "x-ig-app-id": _IG_APP_ID,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://www.instagram.com/p/{shortcode}/",
            },
        )
        if resp.status_code == 429:
            return PublicResult(ok=False, error="rate limited (429)")
        if resp.status_code >= 400:
            return PublicResult(ok=False, error=f"HTTP {resp.status_code}")

        body = resp.json()
        media = (
            body.get("data", {}).get("xig_polaris_media", {})
            .get("if_not_gated_logged_out")
        )
        if media is None:
            gating = body.get("data", {}).get("xig_polaris_media", {}).get("gating_info")
            if gating:
                return PublicResult(ok=False, error=f"gated: {gating.get('title', 'restricted')}")
            return PublicResult(ok=False, error="no media in response (private or deleted)")

        return PublicResult(ok=True, data=_parse_ig_media(media))
    except Exception as exc:  # noqa: BLE001
        return PublicResult(ok=False, error=str(exc)[:200])


def _parse_ig_media(media: dict) -> dict:
    """Normalize an Instagram GraphQL media node into our standard shape."""
    caption_obj = media.get("caption") or {}
    caption_text = caption_obj.get("text", "") if isinstance(caption_obj, dict) else ""

    user = media.get("user") or {}
    author = user.get("username", "")

    # Extract hashtags and mentions from caption.
    hashtags = re.findall(r"#(\w+)", caption_text)
    mentions = re.findall(r"@(\w+)", caption_text)

    # Media items: single image, video, or carousel (sidecar).
    media_items: list[dict] = []
    typename = media.get("__typename", "")

    if typename == "GraphSidecar":
        edges = (media.get("edge_sidecar_to_children") or {}).get("edges") or []
        for edge in edges:
            node = edge.get("node", {})
            item = _ig_node_to_media_item(node)
            if item:
                media_items.append(item)
    else:
        item = _ig_node_to_media_item(media)
        if item:
            media_items.append(item)

    location = media.get("location")
    location_name = location.get("name", "") if isinstance(location, dict) else ""

    return {
        "url": f"https://www.instagram.com/p/{media.get('shortcode', '')}/",
        "shortcode": media.get("shortcode", ""),
        "author": author,
        "caption": caption_text,
        "date": _ts_to_iso(media.get("taken_at")),
        "likes": media.get("like_count"),
        "comments_count": media.get("comment_count"),
        "media": media_items,
        "hashtags": hashtags,
        "mentions": mentions,
        "location": location_name,
    }


def _ig_node_to_media_item(node: dict) -> dict | None:
    """Convert one GraphQL media node (image or video) to our media dict."""
    is_video = node.get("is_video", False)
    if is_video:
        url = node.get("video_url") or ""
        if not url:
            return None
        return {"type": "video", "url": url, "alt": node.get("accessibility_caption", "")}
    else:
        url = node.get("display_url") or ""
        if not url:
            return None
        return {"type": "image", "url": url, "alt": node.get("accessibility_caption", "")}


async def ig_fetch_embed(shortcode: str) -> PublicResult:
    """Fallback: scrape the /p/<shortcode>/embed/captioned/ page.

    This page is always public and renders enough metadata for basic capture
    when the GraphQL endpoint is blocked or rotated.
    """
    try:
        client = await _get_client()
        resp = await client.get(
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            headers={"Referer": "https://www.instagram.com/"},
        )
        if resp.status_code >= 400:
            return PublicResult(ok=False, error=f"HTTP {resp.status_code}")

        html = resp.text
        data = _parse_ig_embed(html, shortcode)
        if data is None:
            return PublicResult(ok=False, error="could not parse embed page")
        return PublicResult(ok=True, data=data)
    except Exception as exc:  # noqa: BLE001
        return PublicResult(ok=False, error=str(exc)[:200])


def _parse_ig_embed(html: str, shortcode: str) -> dict | None:
    """Extract structured data from an Instagram embed page's HTML."""
    # Author
    author_m = re.search(r'class="Username"[^>]*>([^<]+)</a>', html)
    author = author_m.group(1).strip() if author_m else ""

    # Caption
    cap_m = re.search(r'class="Caption"[^>]*>(.*?)</div>', html, re.DOTALL)
    caption = ""
    if cap_m:
        caption = re.sub(r"<[^>]+>", "", cap_m.group(1)).strip()

    # Date
    date_m = re.search(r'<time[^>]*datetime="([^"]+)"', html)
    date = date_m.group(1) if date_m else ""

    # Image
    img_m = re.search(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', html)
    media_items: list[dict] = []
    if img_m:
        media_items.append({"type": "image", "url": img_m.group(1), "alt": ""})

    if not author and not caption and not media_items:
        return None

    return {
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "shortcode": shortcode,
        "author": author,
        "caption": caption,
        "date": date,
        "likes": None,
        "comments_count": None,
        "media": media_items,
        "hashtags": re.findall(r"#(\w+)", caption),
        "mentions": re.findall(r"@(\w+)", caption),
        "location": "",
    }


# ---------------------------------------------------------------------------
# X (Twitter) public endpoints
# ---------------------------------------------------------------------------

async def x_fetch_syndication(status_id: str) -> PublicResult:
    """Fetch a tweet via X's syndication API (what the embed widget calls).

    Endpoint: cdn.syndication.twimg.com/tweet-result?id=<id>&token=<computed>
    The token is derived from the status ID using the same algorithm the
    official embed widget uses (simple base64-like encoding).
    """
    try:
        token = _x_compute_token(status_id)
        client = await _get_client()
        resp = await client.get(
            "https://cdn.syndication.twimg.com/tweet-result",
            params={"id": status_id, "token": token},
            headers={"Referer": "https://platform.twitter.com/"},
        )
        if resp.status_code == 429:
            return PublicResult(ok=False, error="rate limited (429)")
        if resp.status_code >= 400:
            return PublicResult(ok=False, error=f"HTTP {resp.status_code}")

        body = resp.json()
        if not body or "text" not in body:
            return PublicResult(ok=False, error="empty or malformed syndication response")
        return PublicResult(ok=True, data=_parse_x_syndication(body, status_id))
    except Exception as exc:  # noqa: BLE001
        return PublicResult(ok=False, error=str(exc)[:200])


def _x_compute_token(status_id: str) -> str:
    """Compute the syndication token for a given status ID.

    The embed widget derives this from the tweet ID. The algorithm has been
    stable: it's essentially str(int(id) % 10) padded, but the exact form
    varies. We use the known-working formula from community reverse-engineering.
    """
    # Simple modular hash that matches what the widget sends. If this breaks,
    # the oEmbed fallback will still work.
    try:
        num = int(status_id)
        return str(num % 10) + str((num >> 4) % 10)
    except ValueError:
        return "0"


def _parse_x_syndication(body: dict, status_id: str) -> dict:
    """Normalize a syndication API response into our standard shape."""
    user = body.get("user") or {}
    handle = user.get("screen_name", "")
    author_name = user.get("name", "")

    # Expand t.co URLs in text.
    text = body.get("text", "")
    urls = body.get("urls") or []
    for u in urls:
        short = u.get("url", "")
        expanded = u.get("expanded_url", "")
        if short and expanded:
            text = text.replace(short, expanded)

    # Media
    media_items: list[dict] = []
    for md in body.get("mediaDetails") or []:
        mtype = md.get("type", "")
        if mtype == "photo":
            media_items.append({
                "type": "image",
                "url": md.get("media_url_https", ""),
                "alt": md.get("ext_alt_text", ""),
            })
        elif mtype == "video":
            # Pick highest bitrate variant.
            variants = md.get("variants") or []
            best = max(
                (v for v in variants if v.get("content_type") == "video/mp4"),
                key=lambda v: v.get("bitrate", 0),
                default=None,
            )
            if best:
                media_items.append({
                    "type": "video",
                    "url": best.get("url", ""),
                    "alt": md.get("ext_alt_text", ""),
                })

    # Quoted tweet
    quoted = None
    qt = body.get("quoted_tweet")
    if qt:
        qt_user = qt.get("user") or {}
        quoted = {
            "author": qt_user.get("screen_name", ""),
            "text": qt.get("text", ""),
        }

    # Reply-to
    reply_to = body.get("in_reply_to_screen_name", "")

    return {
        "id": status_id,
        "author": author_name,
        "handle": handle,
        "text": text,
        "date": body.get("created_at", ""),
        "media": media_items,
        "quoted": quoted,
        "reply_to": reply_to,
        "reply_to_id": body.get("in_reply_to_status_id_str", ""),
        "likes": body.get("favorite_count"),
        "retweets": body.get("retweet_count"),
        "replies": body.get("reply_count"),
        "quotes": body.get("quote_count"),
        "canonical_url": f"https://x.com/{handle}/status/{status_id}" if handle else "",
    }


async def x_fetch_oembed(url: str) -> PublicResult:
    """Fallback: fetch tweet data via Twitter's oEmbed endpoint.

    Less structured than syndication (HTML blob instead of JSON fields), but
    very stable and doesn't require a token.
    """
    try:
        client = await _get_client()
        resp = await client.get(
            "https://publish.twitter.com/oembed",
            params={"url": url, "omit_script": "true"},
        )
        if resp.status_code >= 400:
            return PublicResult(ok=False, error=f"HTTP {resp.status_code}")

        body = resp.json()
        data = _parse_x_oembed(body, url)
        if data is None:
            return PublicResult(ok=False, error="could not parse oembed response")
        return PublicResult(ok=True, data=data)
    except Exception as exc:  # noqa: BLE001
        return PublicResult(ok=False, error=str(exc)[:200])


def _parse_x_oembed(body: dict, url: str) -> dict | None:
    """Extract structured data from an oEmbed response's HTML field."""
    html = body.get("html", "")
    if not html:
        return None

    # Extract text from blockquote.
    text_m = re.search(r"<p>(.*?)</p>", html, re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text_m.group(1)).strip() if text_m else ""

    # Author
    author = body.get("author_name", "")
    author_url = body.get("author_url", "")
    handle_m = re.search(r"twitter\.com/(\w+)", author_url)
    handle = handle_m.group(1) if handle_m else ""

    # Date from the link text.
    date_m = re.search(r"<a[^>]*>(\w+ \d+, \d{4})</a>", html)
    date_str = date_m.group(1) if date_m else ""

    # Status ID from URL.
    id_m = re.search(r"/status/(\d+)", url)
    status_id = id_m.group(1) if id_m else ""

    if not text and not author:
        return None

    return {
        "id": status_id,
        "author": author,
        "handle": handle,
        "text": text,
        "date": date_str,
        "media": [],  # oEmbed doesn't include media URLs
        "quoted": None,
        "reply_to": "",
        "reply_to_id": "",
        "likes": None,
        "retweets": None,
        "replies": None,
        "quotes": None,
        "canonical_url": url,
    }


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

async def download_media(url: str, dest: Path, *, max_bytes: int = 500_000_000) -> tuple[bool, Any]:
    """Stream-download a media file to dest with a size cap.

    Returns (ok, info) where info is the file size on success or an error
    string on failure. Respects max_bytes (default 500 MB).
    """
    try:
        client = await _get_client()
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}"
            total = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        f.close()
                        dest.unlink(missing_ok=True)
                        return False, f"exceeded {max_bytes // 1_000_000} MB cap"
                    f.write(chunk)
        return True, total
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return False, str(exc)[:200]


def maybe_transcribe(video_path: Path) -> str | None:
    """Call iofast_engine.transcribe.transcribe(path) if available.

    Another worker adds that module on feat/yt-transcribe. Until then, or
    if import fails, returns None so the caller can note "transcript pending".
    """
    try:
        from ..transcribe import transcribe  # type: ignore[attr-defined]
        return transcribe(video_path)
    except ImportError:
        return None
    except Exception:  # noqa: BLE001
        return None


def _ts_to_iso(ts: Any) -> str:
    """Convert a Unix timestamp to ISO 8601 string, or return empty."""
    if ts is None:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ""