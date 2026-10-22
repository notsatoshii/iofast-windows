"""YouTube source: metadata + transcript via yt-dlp, with local whisper fallback.

Uses yt-dlp as a library to extract video info and captions. Each video becomes
one markdown file with envelope; playlists expand to their entries.

Transcript priority:
  1. Uploader-made (manual) captions in the video's language → use them.
  2. Otherwise → download audio-only and transcribe locally with faster-whisper.

Caption language order: opts.lang first, then Korean, then English, then any.
Videos over the configurable length ceiling (default 3 h) skip transcription
and set a result warning instead of running silently.
"""

import asyncio
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..ledger import Ledger
from ..transcribe import (
    TranscriptResult,
    download_audio,
    should_skip_transcribe,
    transcribe_audio,
)
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


def _subtitle_lang_list(preferred: str | None) -> list[str]:
    """Build an exact subtitle language list for yt-dlp.

    Requests only the preferred language plus ko/en fallbacks — never 'all'
    or wildcard patterns like 'en.*', which pull ~30 translated tracks and
    trigger HTTP 429 from YouTube.
    """
    langs: list[str] = []
    if preferred:
        langs.append(preferred)
    for fb in ("ko", "en"):
        if fb not in langs:
            langs.append(fb)
    return langs or ["en"]


def _safe_title_slug(title: str, vid_id: str, max_len: int = 80) -> str:
    """Turn a video title into a safe filename stem with id suffix.

    Keeps unicode letters/digits, replaces everything else with hyphens,
    strips leading/trailing hyphens, truncates to max_len, appends -<id>.
    """
    slug = re.sub(r"[^\w\-]+", "-", title, flags=re.UNICODE).strip("-_")
    if not slug:
        slug = "video"
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return f"{slug}-{vid_id}"


def _has_manual_captions(info: dict, preferred_lang: str | None) -> bool:
    """True when uploader-made captions exist in a usable language."""
    subs = info.get("subtitles") or {}
    if not subs:
        return False
    if preferred_lang and preferred_lang in subs:
        return bool(subs[preferred_lang])
    for lang in ("ko", "en"):
        if lang in subs and subs[lang]:
            return True
    # Any language counts as manual.
    return any(bool(entries) for entries in subs.values())


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


def _video_md(
    info: dict,
    transcript: str,
    *,
    transcript_header: str = "",
    warnings: list[str] | None = None,
) -> str:
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
    if transcript_header:
        parts.append(f"*{transcript_header}*")
        parts.append("")
    if transcript:
        parts.append("## Transcript")
        parts.append("")
        parts.append(transcript)
        parts.append("")

    # Warnings (loud, never silent).
    if warnings:
        parts.append("## ⚠️ Warnings")
        parts.append("")
        for w in warnings:
            parts.append(f"- {w}")
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

        # yt-dlp options: metadata + subtitle download (no media).
        # Request only exact language codes to avoid pulling ~30 translated
        # tracks (en-xx) which triggers HTTP 429 from YouTube.
        sub_langs = _subtitle_lang_list(opts.lang)
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsubtitles": True,
            "subtitleslangs": sub_langs,
            "subtitlesformat": "json3/srv3/vtt/best",
            "extract_flat": False,  # resolve playlist entries fully
            "ignoreerrors": True,   # skip broken entries instead of aborting
        }

        # Extract metadata only first (no subtitle download yet).
        meta_opts = dict(ydl_opts)
        meta_opts["writesubtitles"] = False
        meta_opts["writeautomaticsubtitles"] = False

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, lambda: _extract(meta_opts, url))

        if info is None:
            ledger.record(url, "failed_fetch", "yt-dlp returned no info")
            counts = ledger.counts()
            write_run(str(run_dir), ledger, {"source": "youtube", "url": url})
            return GrabResult(
                source="youtube", run_dir=run_dir,
                ok=counts["ok"], thin=counts.get("thin_escalated_ok", 0), failed=1,
            )

        # Playlist detection: yt-dlp sets 'entries' for playlists.
        entries_list = info.get("entries")
        if entries_list is not None:
            videos = [e for e in entries_list if e is not None]
        else:
            videos = [info]

        writer.max_pages = len(videos)
        ok = thin = failed = 0

        for i, vid in enumerate(videos, 1):
            vid_url = vid.get("webpage_url") or vid.get("original_url") or url
            vid_id = vid.get("id") or ""
            vid_title = vid.get("title") or "Untitled"
            ledger.enqueue(vid_url)

            emit({
                "type": "progress",
                "n": i,
                "total": len(videos),
                "message": f"🎬 {i}/{len(videos)}: {vid_title}",
            })

            # --- Download subtitles to disk and parse them ---
            sub_dir = run_dir / "_subs_tmp"
            downloaded_subs = await loop.run_in_executor(
                None, lambda: _download_subtitles(vid_url, sub_dir, sub_langs, emit)
            )

            # --- Transcript resolution ---
            transcript = ""
            header = ""
            warnings: list[str] = []
            how = "yt-dlp"
            outcome = "thin_escalated_ok"
            status = "no transcript"

            has_manual = _has_manual_captions(vid, opts.lang)
            # Try parsed subtitle files first, then fall back to info dict entries.
            caption_entries = _resolve_caption_entries(
                vid, downloaded_subs, opts.lang
            )
            duration_s = vid.get("duration")

            if has_manual and caption_entries:
                # Rule 1: uploader-made captions win.
                transcript = _transcript_text(caption_entries)
                if transcript.strip():
                    header = "Transcript: uploader captions"
                    how = "yt-dlp+caption"
                    outcome = "ok"
                    status = ""
                else:
                    # Header claims captions but text is empty — fall through.
                    caption_entries = None
                    transcript = ""

            if not transcript.strip() and not should_skip_transcribe(duration_s):
                # Rule 2: no usable captions → local whisper.
                result = await _whisper_fallback(vid_url, run_dir, emit)
                if result and result.text.strip():
                    transcript = result.text
                    lang_tag = f" ({result.language})" if result.language else ""
                    header = f"Transcript: whisper {result.model} (local){lang_tag}"
                    how = "yt-dlp+whisper"
                    outcome = "ok"
                    status = ""
                    warnings.extend(result.warnings)
                elif result and not result.text.strip():
                    # Whisper ran but produced no text — likely no speech.
                    no_speech = not result.warnings
                    if no_speech:
                        warnings.append(
                            "No speech detected in audio. "
                            "Video may be silent or too short."
                        )
                    else:
                        fail_msg = "; ".join(result.warnings)
                        warnings.append(f"Local transcription failed ({fail_msg}).")
                    # Try auto-captions as last resort.
                    auto_entries = _resolve_auto_caption_entries(
                        vid, downloaded_subs, opts.lang
                    )
                    if auto_entries:
                        transcript = _transcript_text(auto_entries)
                        if transcript.strip():
                            header = "Transcript: auto-generated captions (fallback)"
                            how = "yt-dlp+auto-caption"
                            outcome = "ok"
                            status = ""
                else:
                    # Audio download or whisper load failed entirely.
                    fail_msg = ""
                    if result:
                        fail_msg = "; ".join(result.warnings) or "audio download failed"
                    warnings.append(f"Local transcription failed ({fail_msg}).")
                    auto_entries = _resolve_auto_caption_entries(
                        vid, downloaded_subs, opts.lang
                    )
                    if auto_entries:
                        transcript = _transcript_text(auto_entries)
                        if transcript.strip():
                            header = "Transcript: auto-generated captions (fallback)"
                            how = "yt-dlp+auto-caption"
                            outcome = "ok"
                            status = ""

            if not transcript.strip() and should_skip_transcribe(duration_s):
                # Length guard: don't transcribe silently.
                dur_str = _format_duration(duration_s)
                warnings.append(
                    f"Video is {dur_str}; exceeds transcription ceiling "
                    f"({_format_duration(_max_seconds())}). "
                    "Set IOFAST_TRANSCRIBE_MAX_SECONDS to override."
                )
                auto_entries = _resolve_auto_caption_entries(
                    vid, downloaded_subs, opts.lang
                )
                if auto_entries:
                    transcript = _transcript_text(auto_entries)
                    if transcript.strip():
                        header = "Transcript: auto-generated captions (partial)"
                        how = "yt-dlp+auto-caption"
                        outcome = "ok"
                        status = ""

            # Never write a header with no body.
            if header and not transcript.strip():
                header = ""
                if not warnings:
                    warnings.append("No transcript available.")

            body = _video_md(vid, transcript, transcript_header=header, warnings=warnings)

            # Use title-based filename instead of URL-derived watch.md.
            slug = _safe_title_slug(vid_title, vid_id)
            file_stem = slug

            if outcome == "ok":
                writer.write(
                    vid_url, body, tier="static", how=how, outcome="ok",
                    name_override=file_stem,
                )
                ok += 1
            else:
                writer.write(
                    vid_url, body, tier="static", how=how,
                    outcome=outcome, status=status,
                    name_override=file_stem,
                )
                thin += 1

            # Clean up subtitle temp files.
            try:
                import shutil
                shutil.rmtree(sub_dir, ignore_errors=True)
            except Exception:
                pass

        counts = ledger.counts()
        first_title = videos[0].get("title", "") if videos else ""
        info_out = {"source": "youtube", "url": url}
        if first_title:
            info_out["title"] = first_title
        write_run(str(run_dir), ledger, info_out)
        return GrabResult(
            source="youtube", run_dir=run_dir,
            ok=ok, thin=thin, failed=failed,
        )


def _max_seconds() -> int:
    """Read the transcription ceiling (mirrors transcribe.max_transcribe_seconds)."""
    raw = os.environ.get("IOFAST_TRANSCRIBE_MAX_SECONDS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 3 * 3600


async def _whisper_fallback(
    vid_url: str, run_dir: Path, emit
) -> TranscriptResult | None:
    """Download audio + transcribe in a thread. Cleans up temp audio after."""
    loop = asyncio.get_running_loop()
    tmp_dir = run_dir / "_audio_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        audio_path = await loop.run_in_executor(
            None, lambda: download_audio(vid_url, tmp_dir, emit)
        )
        if not audio_path:
            return None
        result = await loop.run_in_executor(
            None, lambda: transcribe_audio(audio_path, emit)
        )
        return result
    finally:
        # Clean up downloaded audio regardless of outcome.
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _extract(ydl_opts: dict, url: str) -> dict | None:
    """Run yt-dlp extraction synchronously. Returns info dict or None on total failure."""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def _download_subtitles(
    url: str, dest_dir: Path, sub_langs: list[str], emit
) -> dict[str, list[dict]]:
    """Download subtitle files via yt-dlp and parse them into entry dicts.

    Returns {lang: [entries]} for each successfully downloaded track.
    Uses skip_download=True so only subtitles are fetched, not media.
    """
    import json
    import glob as globmod

    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "%(id)s")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsubtitles": True,
        "subtitleslangs": sub_langs,
        "subtitlesformat": "json3/srv3/vtt/best",
        "outtmpl": outtmpl,
        "ignoreerrors": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        emit({"type": "warning", "message": f"subtitle download failed: {exc}"})
        return {}

    result: dict[str, list[dict]] = {}
    # Look for downloaded subtitle files: <id>.<lang>.json3, .srv3, .vtt, etc.
    for sub_file in sorted(dest_dir.iterdir()):
        if not sub_file.is_file():
            continue
        name = sub_file.name
        # Match patterns like <id>.en.json3, <id>.ko.vtt, <id>.en.srv3
        parts = name.split(".")
        if len(parts) < 3:
            continue
        lang = parts[-2]
        ext = parts[-1]
        if ext not in ("json3", "srv3", "vtt", "srt", "ass"):
            continue

        entries = _parse_subtitle_file(sub_file, ext)
        if entries:
            # Prefer manual over auto; keep first successful parse per lang.
            if lang not in result:
                result[lang] = entries

    return result


def _parse_subtitle_file(path: Path, ext: str) -> list[dict]:
    """Parse a subtitle file into [{text, start, duration}] entries."""
    import json

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []

    if ext == "json3":
        return _parse_json3(raw)
    elif ext == "srv3":
        return _parse_srv3(raw)
    elif ext == "vtt":
        return _parse_vtt(raw)
    elif ext == "srt":
        return _parse_srt(raw)
    return []


def _parse_json3(raw: str) -> list[dict]:
    """Parse YouTube json3 subtitle format."""
    import json
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    events = data.get("events") or []
    entries: list[dict] = []
    for ev in events:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        start_ms = ev.get("tStartMs", 0)
        dur_ms = ev.get("dDurationMs", 0)
        entries.append({
            "text": text,
            "start": start_ms / 1000.0,
            "duration": dur_ms / 1000.0,
        })
    return entries


def _parse_srv3(raw: str) -> list[dict]:
    """Parse YouTube srv3 XML subtitle format."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    entries: list[dict] = []
    for body in root.iter("body"):
        for p in body.iter("p"):
            text = (p.text or "").strip()
            if not text:
                continue
            start_ms = int(p.get("t", "0"))
            dur_ms = int(p.get("d", "0"))
            entries.append({
                "text": text,
                "start": start_ms / 1000.0,
                "duration": dur_ms / 1000.0,
            })
    return entries


def _parse_vtt(raw: str) -> list[dict]:
    """Parse WebVTT subtitle format."""
    import re as _re
    entries: list[dict] = []
    # Match timestamp lines: 00:00:00.000 --> 00:00:05.000
    ts_pattern = _re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})"
    )
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        m = ts_pattern.match(lines[i].strip())
        if m:
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
            end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000.0
            text_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip():
                # Strip VTT tags like <c>, </c>, etc.
                clean = _re.sub(r"<[^>]+>", "", lines[i]).strip()
                if clean:
                    text_lines.append(clean)
                i += 1
            text = " ".join(text_lines).strip()
            if text:
                entries.append({"text": text, "start": start, "duration": end - start})
            continue
        i += 1
    return entries


def _parse_srt(raw: str) -> list[dict]:
    """Parse SRT subtitle format."""
    import re as _re
    entries: list[dict] = []
    ts_pattern = _re.compile(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
    )
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        m = ts_pattern.match(lines[i].strip())
        if m:
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
            end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000.0
            text_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            text = " ".join(text_lines).strip()
            if text:
                entries.append({"text": text, "start": start, "duration": end - start})
            continue
        i += 1
    return entries


def _resolve_caption_entries(
    info: dict, downloaded_subs: dict[str, list[dict]], preferred_lang: str | None
) -> list[dict] | None:
    """Pick manual caption entries from downloaded subtitle files or info dict.

    Prefers parsed files on disk (they contain actual text); falls back to
    the info dict's subtitle entries (which may be empty when download=False
    was used for metadata extraction).
    """
    subs = info.get("subtitles") or {}
    candidates: list[str] = []
    if preferred_lang:
        candidates.append(preferred_lang)
    for fb in ("ko", "en"):
        if fb not in candidates:
            candidates.append(fb)

    # Try downloaded files first (they have real text content).
    for lang in candidates:
        if lang in downloaded_subs and downloaded_subs[lang]:
            return downloaded_subs[lang]
    # Any downloaded manual sub.
    for lang, entries in downloaded_subs.items():
        if entries and lang not in (info.get("automatic_captions") or {}):
            return entries

    # Fall back to info dict entries (may be empty if subtitles weren't downloaded).
    for lang in candidates:
        if lang in subs and subs[lang]:
            return subs[lang]
    for lang, entries in subs.items():
        if entries:
            return entries

    return None


def _resolve_auto_caption_entries(
    info: dict, downloaded_subs: dict[str, list[dict]], preferred_lang: str | None
) -> list[dict] | None:
    """Pick auto-generated caption entries, excluding manual tracks.

    Used as a last-resort fallback when manual captions and whisper both fail.
    """
    auto = info.get("automatic_captions") or {}
    manual_langs = set((info.get("subtitles") or {}).keys())

    candidates: list[str] = []
    if preferred_lang:
        candidates.append(preferred_lang)
    for fb in ("ko", "en"):
        if fb not in candidates:
            candidates.append(fb)

    # Downloaded subs that aren't manual.
    for lang in candidates:
        if lang in downloaded_subs and downloaded_subs[lang] and lang not in manual_langs:
            return downloaded_subs[lang]

    # Info dict auto captions.
    for lang in candidates:
        if lang in auto and auto[lang]:
            return auto[lang]
    for lang, entries in auto.items():
        if entries:
            return entries

    return None