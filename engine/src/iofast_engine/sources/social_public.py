"""Public (no-login) endpoints for Instagram and X captures.

Instagram: uses yt-dlp (already a dependency) as the primary public route.
yt-dlp handles TLS fingerprinting, LSD tokens, and GraphQL doc_id rotation
that raw httpx requests cannot reliably reproduce without browser impersonation.
Falls back to /p/<shortcode>/embed/captioned/ scraping if yt-dlp fails.

X: syndication API (cdn.syndication.twimg.com/tweet-result) with html.unescape
and t.co expansion from entities.urls. Falls back to oEmbed.

Transcribe hook: if ``iofast_engine.transcribe`` is importable (added by
another worker on feat/yt-transcribe), ``maybe_transcribe(path)`` calls its
``transcribe(path)`` and returns the text; otherwise returns None and the
caller notes "transcript pending".
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# httpx is a declared dependency (pyproject.toml); imported lazily so tests
# can stub without installing the full stack.
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
# Instagram public endpoints (yt-dlp primary, embed fallback)
# ---------------------------------------------------------------------------

async def ig_fetch_post_ytdlp(url: str) -> PublicResult:
    """Fetch a public Instagram post/reel/carousel via yt-dlp --simulate -j.

    yt-dlp handles the logged-out GraphQL flow with proper TLS impersonation,
    LSD token fetching, and doc_id rotation that raw HTTP cannot replicate.
    Returns parsed metadata dict on success.
    """
    try:
        # Find the venv python that has yt-dlp installed.
        py = sys.executable
        proc = await asyncio.create_subprocess_exec(
            py, "-m", "yt_dlp", "--simulate", "-j", "--no-warnings", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0 or not stdout.strip():
            err_msg = stderr.decode("utf-8", errors="replace")[:200]
            return PublicResult(ok=False, error=f"yt-dlp failed: {err_msg}")

        info = json.loads(stdout.decode("utf-8").split("\n")[0])
        return PublicResult(ok=True, data=_parse_ytdlp_info(info, url))
    except asyncio.TimeoutError:
        return PublicResult(ok=False, error="yt-dlp timed out (30s)")
    except Exception as exc:  # noqa: BLE001
        return PublicResult(ok=False, error=str(exc)[:200])


def _parse_ytdlp_info(info: dict, url: str) -> dict:
    """Normalize yt-dlp info_dict into our standard post shape."""
    caption = info.get("description") or info.get("title") or ""
    hashtags = re.findall(r"#(\w+)", caption)
    mentions = re.findall(r"@(\w+)", caption)

    # Media items from formats (video) or thumbnails (image).
    media_items: list[dict] = []
    formats = info.get("formats") or []
    # Pick best video format if present. Instagram serves DASH video-only and
    # audio-only streams next to progressive ones with sound (codecs often
    # unknown), so prefer anything not marked audio-less; yt-dlp lists formats
    # worst to best, which breaks ties.
    video_formats = [
        (i, f) for i, f in enumerate(formats)
        if f.get("vcodec") != "none" and f.get("url")
    ]
    if video_formats:
        with_sound = [(i, f) for i, f in video_formats if f.get("acodec") != "none"]
        pool = with_sound or video_formats
        _, best = max(pool, key=lambda p: (p[1].get("filesize") or p[1].get("tbr") or 0, p[0]))
        item = {
            "type": "video",
            "url": best["url"],
            "alt": info.get("description") or "",
            "duration": info.get("duration"),
        }
        # A separate audio stream is what transcription needs when the chosen
        # video has none.
        audio_formats = [
            (i, f) for i, f in enumerate(formats)
            if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none") and f.get("url")
        ]
        if audio_formats:
            _, best_audio = max(audio_formats, key=lambda p: (p[1].get("tbr") or 0, p[0]))
            item["audio_url"] = best_audio["url"]
        media_items.append(item)
    # Images: thumbnail or entries from thumbnails list.
    thumbs = info.get("thumbnails") or []
    if thumbs:
        # Pick highest resolution thumbnail.
        best_thumb = max(thumbs, key=lambda t: t.get("width") or t.get("height") or 0)
        if best_thumb.get("url"):
            media_items.append({
                "type": "image",
                "url": best_thumb["url"],
                "alt": info.get("description") or "",
            })
    elif info.get("thumbnail"):
        media_items.append({
            "type": "image",
            "url": info["thumbnail"],
            "alt": info.get("description") or "",
        })

    # Extract shortcode from URL.
    sc_m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    shortcode = sc_m.group(1) if sc_m else ""

    return {
        "url": url,
        "shortcode": shortcode,
        "author": info.get("uploader") or info.get("channel") or "",
        "caption": caption,
        "date": info.get("upload_date") or "",
        "likes": info.get("like_count"),
        "comments_count": info.get("comment_count"),
        "media": media_items,
        "hashtags": hashtags,
        "mentions": mentions,
        "location": "",
        "view_count": info.get("view_count"),
    }


async def ig_fetch_embed(shortcode: str) -> PublicResult:
    """Fallback: scrape the /p/<shortcode>/embed/captioned/ page."""
    try:
        client = await _get_client()
        resp = await client.get(
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            headers={"Referer": "https://www.instagram.com/"},
        )
        if resp.status_code >= 400:
            return PublicResult(ok=False, error=f"HTTP {resp.status_code}")
        data = _parse_ig_embed(resp.text, shortcode)
        if data is None:
            return PublicResult(ok=False, error="could not parse embed page")
        return PublicResult(ok=True, data=data)
    except Exception as exc:  # noqa: BLE001
        return PublicResult(ok=False, error=str(exc)[:200])


def _parse_ig_embed(html_text: str, shortcode: str) -> dict | None:
    """Extract structured data from an Instagram embed page's HTML."""
    author_m = re.search(r'class="Username"[^>]*>([^<]+)</a>', html_text)
    author = author_m.group(1).strip() if author_m else ""

    cap_m = re.search(r'class="Caption"[^>]*>(.*?)</div>', html_text, re.DOTALL)
    caption = re.sub(r"<[^>]+>", "", cap_m.group(1)).strip() if cap_m else ""

    date_m = re.search(r'<time[^>]*datetime="([^"]+)"', html_text)
    date = date_m.group(1) if date_m else ""

    media_items: list[dict] = []
    img_m = re.search(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', html_text)
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
    """Fetch a tweet via X's syndication API with html.unescape and t.co expansion."""
    try:
        token = _x_compute_token(status_id)
        client = await _get_client()
        resp = await client.get(
            "https://cdn.syndication.twimg.com/tweet-result",
            params={
                "id": status_id,
                "token": token,
                "lang": "en",
                "features": "tfw_timeline_list:;tfw_follower_count_sunset:true;tfw_tweet_edit_backend:on;tfw_refsrc_session:on;tfw_show_blue_verified_badge:on;tfw_legacy_timeline_sunset:true;tfw_tweet_edit_frontend:on",
            },
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
    """Compute the syndication token the embed widget sends.

    Port of react-tweet's getToken: ((id / 1e15) * PI).toString(36) with zeros
    and the dot removed. X does not check it today, but may start to.
    """
    try:
        x = (int(status_id) / 1e15) * math.pi
    except ValueError:
        return "0"
    return re.sub(r"(0+|\.)", "", _js_float_to_base36(x))


def _js_float_to_base36(x: float) -> str:
    """Number.prototype.toString(36) for a positive float (V8's DoubleToRadixCString)."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    integer = int(x)
    fraction = x - integer
    out = ""
    while True:
        out = digits[integer % 36] + out
        integer //= 36
        if integer == 0:
            break
    # Emit fraction digits only while they are still meaningful for a double.
    delta = max(0.5 * (math.nextafter(x, math.inf) - x), math.nextafter(0.0, 1.0))
    if fraction >= delta:
        frac = []
        while True:
            fraction *= 36
            delta *= 36
            digit = int(fraction)
            frac.append(digit)
            fraction -= digit
            if (fraction > 0.5 or (fraction == 0.5 and digit & 1)) and fraction + delta > 1:
                # Round up, carrying into earlier digits.
                while frac:
                    if frac[-1] + 1 < 36:
                        frac[-1] += 1
                        break
                    frac.pop()
                break
            if fraction < delta:
                break
        out += "." + "".join(digits[d] for d in frac)
    return out


def _parse_x_syndication(body: dict, status_id: str) -> dict:
    """Normalize syndication response with html.unescape and t.co expansion."""
    user = body.get("user") or {}
    handle = user.get("screen_name", "")
    author_name = user.get("name", "")

    # Unescape HTML entities first, then expand t.co links.
    text = html.unescape(body.get("text", ""))

    # Expand t.co URLs from entities.urls (more reliable than top-level urls).
    entities = body.get("entities") or {}
    url_entities = entities.get("urls") or body.get("urls") or []
    for u in url_entities:
        short = u.get("url", "")
        expanded = u.get("expanded_url", "")
        if short and expanded:
            text = text.replace(short, expanded)

    # Media from mediaDetails.
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
            variants = md.get("video_info", {}).get("variants") or md.get("variants") or []
            mp4s = [v for v in variants if "mp4" in (v.get("content_type", ""))]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                media_items.append({
                    "type": "video",
                    "url": best.get("url", ""),
                    "alt": md.get("ext_alt_text", ""),
                })

    # Quoted tweet.
    quoted = None
    qt = body.get("quoted_tweet")
    if qt and isinstance(qt, dict):
        qt_user = qt.get("user") or {}
        quoted = {
            "author": qt_user.get("screen_name", ""),
            "text": html.unescape(qt.get("text", "")),
        }

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
        "replies": body.get("conversation_count") or body.get("reply_count"),
        "quotes": body.get("quote_count"),
        "canonical_url": f"https://x.com/{handle}/status/{status_id}" if handle else "",
    }


async def x_fetch_oembed(url: str) -> PublicResult:
    """Fallback: fetch tweet data via Twitter's oEmbed endpoint."""
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
    raw_html = body.get("html", "")
    if not raw_html:
        return None

    text_m = re.search(r"<p>(.*?)</p>", raw_html, re.DOTALL)
    text = html.unescape(re.sub(r"<[^>]+>", "", text_m.group(1))).strip() if text_m else ""

    author = body.get("author_name", "")
    author_url = body.get("author_url", "")
    handle_m = re.search(r"(?:twitter|x)\.com/(\w+)", author_url)
    handle = handle_m.group(1) if handle_m else ""

    id_m = re.search(r"/status/(\d+)", url)
    status_id = id_m.group(1) if id_m else ""

    if not text and not author:
        return None

    return {
        "id": status_id,
        "author": author,
        "handle": handle,
        "text": text,
        "date": "",
        "media": [],
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
    """Stream-download a media file to dest with a size cap."""
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


def maybe_transcribe(video_path: Path, emit=None) -> str | None:
    """Transcribe a local video file via iofast_engine.transcribe.

    Returns the formatted transcript string (with header) on success, or an
    error description string on failure. Returns None only when the
    transcribe module is entirely unavailable (should not happen in normal
    installs since it ships with the engine).

    Never swallows errors silently — callers should treat any non-None,
    non-transcript return as a warning to surface in the note.
    """
    if emit is None:
        emit = lambda _e: None  # noqa: E731
    try:
        from ..transcribe import transcribe_local_video
    except ImportError:
        return None
    try:
        return transcribe_local_video(video_path, emit)
    except Exception as exc:  # noqa: BLE001
        return f"transcription error: {exc}"


def _ts_to_iso(ts: Any) -> str:
    """Convert a Unix timestamp to ISO 8601 string, or return empty."""
    if ts is None:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ""