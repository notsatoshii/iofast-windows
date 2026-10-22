"""YouTube source: metadata + transcript via yt-dlp, no media download.

Uses yt-dlp as a library (download=False) to extract video info and captions.
Each video becomes one markdown file with envelope; playlists expand to their
entries. Caption priority: manual over auto; opts.lang first, then Korean,
then English, then whatever exists. No transcript = thin outcome.
"""

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..ledger import Ledger
from ..writer import Writer, write_run
from .base import GrabOptions, GrabResult

# URL patterns this source claims. Order matters in the registry but not here;
# matches() is called per-source and the first True wins at the detect layer.
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def _is_youtube(url: str) -> bool:
    """True for any YouTube URL shape we handle."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if host not in _YT_HOSTS:
        return False
    path = urlparse(url).path
    # /watch?v=, /shorts/<id>, /playlist?list=, youtu.be/<id>
    return bool(
        path.startswith("/watch")
        or path.startswith("/shorts/")
        or path.startswith("/playlist")
        or host == "youtu.be"
    )


def _format_duration(seconds: int | None) -> str:
    """Seconds -> H:MM:SS or M:SS. Returns '' when unknown."""
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _pick_caption(info: dict, preferred_lang: str | None) -> list[dict] | None:
    """Select caption tracks by priority: manual > auto, preferred lang first.

    Returns the subtitle entries list (already fetched by yt-dlp when
    writesubtitles/writeautomaticsubtitles are set), or None if nothing usable.
    """
    # Manual subtitles take priority over auto-generated ones.
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    # Build candidate language list: user preference, then ko, then en, then any.
    candidates: list[str] = []
    if preferred_lang:
        candidates.append(preferred_lang)
    for fallback in ("ko", "en"):
        if fallback not in candidates:
            candidates.append(fallback)

    # Try manual first, then auto, walking the candidate list each time.
    for pool in (subs, auto):
        if not pool:
            continue
        for lang in candidates:
            if lang in pool:
                return pool[lang]
        # No candidate matched — take whatever language exists in this pool.
        for lang, entries in pool.items():
            if entries:
                return entries

    return None


def _transcript_text(entries: list[dict]) -> str:
    """Merge caption entries into readable paragraphs with [mm:ss] timestamps.

    Groups consecutive lines into paragraphs separated by blank lines, keeping
    the timestamp of each segment's start so readers can locate moments.
    """
    if not entries:
        return ""
    lines: list[str] = []
    current_para: list[str] = []
    last_end: float | None = None

    for entry in entries:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        start = entry.get("start", 0)
        end = entry.get("duration", 0) + start
        ts = f"[{int(start) // 60:02d}:{int(start) % 60:02d}]"

        # Break paragraph on gaps > 2s or when the previous segment ended.
        if current_para and last_end is not None and (start - last_end) > 2.0:
            lines.append(" ".join(current_para))
            lines.append("")
            current_para = []

        current_para.append(f"{ts} {text}")
        last_end = end

    if current_para:
        lines.append(" ".join(current_para))

    return "\n".join(lines)


def _video_md(info: dict, transcript: str) -> str:
    """Build the markdown body for one video (without envelope)."""
    parts: list[str] = []
    title = info.get("title") or "Untitled"
    channel = info.get("channel") or info.get("uploader") or "Unknown"
    upload_date = info.get("upload_date") or ""
    if upload_date and len(upload_date) == 8:
        try:
            dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            upload_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    duration = _format_duration(info.get("duration"))
    url = info.get("webpage_url") or info.get("original_url") or ""

    parts.append(f"# {title}")
    parts.append("")
    meta_lines = [f"**Channel**: {channel}"]
    if upload_date:
        meta_lines.append(f"**Date**: {upload_date}")
    if duration:
        meta_lines.append(f"**Duration**: {duration}")
    if url:
        meta_lines.append(f"**URL**: {url}")
    parts.extend(meta_lines)
    parts.append("")

    # Chapters, if present.
    chapters = info.get("chapters") or []
    if chapters:
        parts.append("## Chapters")
        parts.append("")
        for ch in chapters:
            ch_start = ch.get("start_time", 0)
            ch_ts = f"[{int(ch_start) // 60:02d}:{int(ch_start) % 60:02d}]"
            ch_title = ch.get("title") or "Untitled chapter"
            parts.append(f"- {ch_ts} {ch_title}")
        parts.append("")

    # Description.
    desc = (info.get("description") or "").strip()
    if desc:
        parts.append("## Description")
        parts.append("")
        parts.append(desc)
        parts.append("")

    # Transcript.
    if transcript:
        parts.append("## Transcript")
        parts.append("")
        parts.append(transcript)
        parts.append("")

    return "\n".join(parts)


class YouTubeSource:
    """Grab YouTube videos/playlists as markdown with transcripts."""

    name = "youtube"

    def matches(self, url: str) -> bool:
        return _is_youtube(url)

    async def grab(
        self, url: str, out_root: Path, opts: GrabOptions, emit
    ) -> GrabResult:
        """Extract metadata + transcript, write one .md per video, produce run artifacts."""
        run_dir = out_root / "youtube"
        run_dir.mkdir(parents=True, exist_ok=True)

        ledger = Ledger()
        writer = Writer(str(run_dir), ledger, max_pages=0, emit=emit)

        # yt-dlp options: metadata only, fetch subtitles but never download media.
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsubtitles": True,
            "subtitleslangs": ["all"],
            "extract_flat": False,  # resolve playlist entries fully
            "ignoreerrors": True,   # skip broken entries instead of aborting
        }

        # Extract info in a thread so the event loop isn't blocked.
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: _extract(ydl_opts, url))

        if info is None:
            ledger.record(url, "failed_fetch", "yt-dlp returned no info")
            counts = ledger.counts()
            write_run(str(run_dir), ledger, {"source": "youtube", "url": url})
            return GrabResult(
                source="youtube", run_dir=run_dir,
                ok=counts["ok"], thin=counts.get("thin_escalated_ok", 0), failed=1,
            )

        # Playlist detection: yt-dlp sets 'entries' for playlists.
        entries = info.get("entries")
        if entries is not None:
            videos = [e for e in entries if e is not None]
        else:
            videos = [info]

        writer.max_pages = len(videos)
        ok = thin = failed = 0

        for i, vid in enumerate(videos, 1):
            vid_url = vid.get("webpage_url") or vid.get("original_url") or url
            ledger.enqueue(vid_url)

            # Pick and render transcript.
            caption_entries = _pick_caption(vid, opts.lang)
            transcript = _transcript_text(caption_entries) if caption_entries else ""

            body = _video_md(vid, transcript)
            has_transcript = bool(transcript.strip())

            if has_transcript:
                writer.write(vid_url, body, tier="static", how="yt-dlp+caption", outcome="ok")
                ok += 1
            else:
                # Still save the metadata-only file, but mark thin so the report flags it.
                writer.write(
                    vid_url, body, tier="static", how="yt-dlp",
                    outcome="thin_escalated_ok", status="no transcript",
                )
                thin += 1

            emit({
                "type": "progress",
                "n": i,
                "total": len(videos),
                "message": f"🎬 {i}/{len(videos)}: {vid.get('title', vid_url)}",
            })

        counts = ledger.counts()
        write_run(str(run_dir), ledger, {"source": "youtube", "url": url})
        return GrabResult(
            source="youtube", run_dir=run_dir,
            ok=ok, thin=thin, failed=failed,
        )


def _extract(ydl_opts: dict, url: str) -> dict | None:
    """Run yt-dlp extraction synchronously. Returns info dict or None on total failure."""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None