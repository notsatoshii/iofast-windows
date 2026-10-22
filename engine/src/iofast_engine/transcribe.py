"""Local speech-to-text via faster-whisper for YouTube videos without captions.

Downloads audio-only via yt-dlp, transcribes with faster-whisper (CUDA float16
when available, else CPU int8). Models live in %LOCALAPPDATA%/iofast/models.
Never calls a cloud API.

Public surface:
    transcribe_audio(audio_path, emit) -> TranscriptResult
    download_audio(url, dest_dir, emit) -> Path | None
    pick_device_and_model() -> tuple[str, str, str]  # (device, compute, model)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp


# ---------------------------------------------------------------------------
# PyAV compatibility shim
# ---------------------------------------------------------------------------
# faster-whisper calls av.open(..., metadata_errors="ignore") which was added
# in PyAV 13 and removed again in PyAV 15+. On Python 3.13 + Windows only
# PyAV 19 has prebuilt wheels, so we patch av.open to silently drop the kwarg
# when the installed version doesn't accept it. This is safe: metadata_errors
# only controls how broken tags are handled during container open, not audio
# decoding itself.
def _patch_av_open():
    try:
        import av
        import inspect
        # Check if av.open accepts metadata_errors by trying a no-op call.
        # We can't inspect.signature on builtins, so we test empirically.
        try:
            # If this raises TypeError about metadata_errors, we need the patch.
            sig = None
            try:
                sig = inspect.signature(av.open)
            except (ValueError, TypeError):
                pass
            if sig and "metadata_errors" in sig.parameters:
                return  # already supported
            # No signature available or param missing — install wrapper.
            _original_av_open = av.open

            def _patched_av_open(*args, **kwargs):
                kwargs.pop("metadata_errors", None)
                return _original_av_open(*args, **kwargs)

            av.open = _patched_av_open
        except TypeError:
            pass
    except ImportError:
        pass  # av not installed yet; faster-whisper will fail with a clear error


_patch_av_open()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _models_root() -> Path:
    """Where whisper models are cached. %LOCALAPPDATA%/iofast/models."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "iofast" / "models"


def pick_device_and_model() -> tuple[str, str, str]:
    """Choose (device, compute_type, model_name) based on hardware + env.

    Env IOFAST_WHISPER_MODEL overrides the model name. Returns one of:
      ("cuda", "float16", "large-v3-turbo")   — GPU present + CUDA libs OK
      ("cpu",  "int8",    "small")             — fallback
    """
    env_model = os.environ.get("IOFAST_WHISPER_MODEL", "").strip()

    # Probe CUDA availability without importing torch (faster-whisper uses
    # ctranslate2 which has its own CUDA probe). We try a tiny import-time
    # check; if it fails we fall back to CPU.
    device = "cpu"
    compute = "int8"
    default_model = "small"

    try:
        import ctranslate2  # noqa: F401  — probe only
        # ctranslate2 exposes cuda_is_available() on builds that ship CUDA.
        if hasattr(ctranslate2, "cuda_is_available") and ctranslate2.cuda_is_available():
            device = "cuda"
            compute = "float16"
            default_model = "large-v3-turbo"
    except Exception:
        pass

    model = env_model or default_model
    return device, compute, model


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def download_audio(
    url: str,
    dest_dir: Path,
    emit: Callable,
    *,
    timeout_s: float = 600,
) -> Path | None:
    """Download best audio-only stream to dest_dir via yt-dlp. Returns path or None.

    No ffmpeg conversion: yt-dlp picks the best native audio format. The file
    is deleted by the caller after transcription.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        # Keep original container; faster-whisper reads most formats directly.
        "postprocessors": [],
    }

    def _progress(d):
        if d.get("status") == "downloading":
            pct = d.get("_percent_str", "?%").strip()
            speed = d.get("_speed_str", "?").strip()
            emit({"type": "progress", "message": f"🎧 audio {pct} @ {speed}"})
        elif d.get("status") == "finished":
            emit({"type": "progress", "message": "🎧 audio done"})

    ydl_opts["progress_hooks"] = [_progress]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        emit({"type": "warning", "message": f"audio download failed: {exc}"})
        return None

    if not info:
        return None

    # Resolve the actual filename yt-dlp wrote.
    ext = info.get("ext") or "m4a"
    vid_id = info.get("id") or "audio"
    candidate = dest_dir / f"{vid_id}.{ext}"
    if candidate.exists():
        return candidate

    # Fallback: find any new file in dest_dir.
    files = sorted(dest_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

@dataclass
class TranscriptResult:
    """Output of a local whisper transcription."""

    text: str               # timestamped paragraphs, [mm:ss] prefix
    language: str           # detected language code (e.g. "en", "ko")
    model: str              # model name used
    device: str             # "cuda" or "cpu"
    warnings: list[str]     # non-fatal issues (e.g. fell back to CPU)


def _group_segments(segments, target_seconds: float = 45.0) -> list[tuple[float, str]]:
    """Merge whisper segments into ~target_seconds paragraphs.

    Returns list of (start_seconds, paragraph_text). Each paragraph is prefixed
    with [mm:ss] by the caller.
    """
    if not segments:
        return []

    paragraphs: list[tuple[float, str]] = []
    buf_start = segments[0].start
    buf_parts: list[str] = []
    last_end = segments[0].start

    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        gap = seg.start - last_end
        duration = last_end - buf_start
        # Break on silence > 2s or when paragraph exceeds target length.
        if buf_parts and (gap > 2.0 or duration >= target_seconds):
            paragraphs.append((buf_start, " ".join(buf_parts)))
            buf_start = seg.start
            buf_parts = []
        buf_parts.append(text)
        last_end = seg.end

    if buf_parts:
        paragraphs.append((buf_start, " ".join(buf_parts)))

    return paragraphs


def _format_paragraphs(paragraphs: list[tuple[float, str]]) -> str:
    """Render (start, text) pairs as [mm:ss] paragraph lines."""
    lines: list[str] = []
    for start, text in paragraphs:
        mm = int(start) // 60
        ss = int(start) % 60
        lines.append(f"[{mm:02d}:{ss:02d}] {text}")
    return "\n\n".join(lines)


def transcribe_audio(
    audio_path: Path,
    emit: Callable,
    *,
    model_override: str | None = None,
) -> TranscriptResult:
    """Transcribe a local audio file with faster-whisper. Never raises.

    On failure returns a TranscriptResult with empty text and a warning.
    """
    warnings: list[str] = []
    device, compute, model_name = pick_device_and_model()
    if model_override:
        model_name = model_override

    models_dir = _models_root()
    models_dir.mkdir(parents=True, exist_ok=True)

    emit({
        "type": "progress",
        "message": f"🗣️ loading whisper {model_name} on {device} ({compute})",
    })

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return TranscriptResult(
            text="", language="", model=model_name, device=device,
            warnings=["faster-whisper not installed — pip install faster-whisper"],
        )

    try:
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute,
            download_root=str(models_dir),
        )
    except Exception as exc:
        # CUDA misconfigured → retry on CPU.
        if device == "cuda":
            warnings.append(
                f"CUDA load failed ({exc}); falling back to CPU int8. "
                "For GPU: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
            )
            device = "cpu"
            compute = "int8"
            model_name = model_override or "small"
            emit({"type": "warning", "message": warnings[-1]})
            try:
                model = WhisperModel(
                    model_name, device=device, compute_type=compute,
                    download_root=str(models_dir),
                )
            except Exception as exc2:
                return TranscriptResult(
                    text="", language="", model=model_name, device=device,
                    warnings=[*warnings, f"CPU load also failed: {exc2}"],
                )
        else:
            return TranscriptResult(
                text="", language="", model=model_name, device=device,
                warnings=[f"whisper load failed: {exc}"],
            )

    emit({"type": "progress", "message": f"🗣️ transcribing {audio_path.name}"})

    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True,
        )
        # Materialise segments (generator is lazy).
        segments = list(segments_iter)
    except Exception as exc:
        return TranscriptResult(
            text="", language="", model=model_name, device=device,
            warnings=[*warnings, f"transcription failed: {exc}"],
        )

    language = getattr(info, "language", "") or ""
    paragraphs = _group_segments(segments)
    text = _format_paragraphs(paragraphs)

    emit({"type": "progress", "message": "🗣️ transcription complete"})
    return TranscriptResult(
        text=text, language=language, model=model_name,
        device=device, warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Length guard
# ---------------------------------------------------------------------------

# Default ceiling: 3 hours. Configurable via IOFAST_TRANSCRIBE_MAX_SECONDS.
_DEFAULT_MAX_SECONDS = 3 * 3600


def max_transcribe_seconds() -> int:
    """Configurable ceiling for auto-transcription. 0 = no limit."""
    raw = os.environ.get("IOFAST_TRANSCRIBE_MAX_SECONDS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return _DEFAULT_MAX_SECONDS


def should_skip_transcribe(duration_s: int | None) -> bool:
    """True when the video exceeds the transcription ceiling."""
    if not duration_s:
        return False
    ceiling = max_transcribe_seconds()
    if ceiling <= 0:
        return False
    return duration_s > ceiling