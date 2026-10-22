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


def ytdlp_js_runtimes() -> dict:
    """Return a js_runtimes dict for yt-dlp that uses patchright's bundled node.exe.

    yt-dlp >= 2024 requires a JS runtime for YouTube signature/n-challenge solving.
    Deno is the default but is not installed on user machines. Patchright ships a
    node.exe in its driver folder that works as a drop-in runtime. We also enable
    the ejs:github remote component so challenge solver scripts can be fetched.

    Returns {} when patchright's node.exe cannot be located (dev/test without
    patchright installed); callers should merge this into their ydl_opts.
    """
    try:
        import patchright
        node_exe = Path(patchright.__file__).parent / "driver" / "node.exe"
        if node_exe.is_file():
            return {
                "js_runtimes": {"node": {"path": str(node_exe)}},
                "remote_components": ["ejs:github"],
            }
    except Exception:
        pass
    return {}


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
    # yt-dlp >= 2024 requires a JS runtime for YouTube signature solving.
    ydl_opts.update(ytdlp_js_runtimes())

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

    # Guard: check if the file has any audio streams before transcribing.
    # Videos with no audio stream cause "tuple index out of range" inside
    # faster-whisper's PyAV backend when it tries container.streams.audio[0].
    try:
        import av as _av
        with _av.open(str(audio_path)) as _container:
            if not _container.streams.audio:
                return TranscriptResult(
                    text="", language="", model=model_name, device=device,
                    warnings=[*warnings, "no audio stream in file"],
                )
    except Exception:
        pass  # If probe fails, let whisper handle it

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

    # Retry without VAD if VAD filtered everything out but audio exists.
    # Music, singing, and some short-form content defeat Silero VAD.
    if not segments:
        try:
            emit({"type": "progress", "message": "🗣️ retrying without VAD filter"})
            segments_iter2, info2 = model.transcribe(
                str(audio_path),
                beam_size=5,
                vad_filter=False,
            )
            segments = list(segments_iter2)
            language = getattr(info2, "language", "") or language
        except Exception:
            pass  # Keep empty segments; will report "no speech detected"

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


# ---------------------------------------------------------------------------
# Public helper for social sources (Instagram, X)
# ---------------------------------------------------------------------------

def transcribe_local_video(
    video_path: Path,
    emit: Callable,
) -> str:
    """Transcribe a local video file and return a formatted transcript string.

    Uses the same model choice, duration cap, and models folder as YouTube.
    faster-whisper reads video containers directly via PyAV (the shim above
    handles version differences).

    Returns a string that includes the header line matching YouTube's format:
        "Transcript: whisper small (local) (en)\\n\\n[00:00] ..."

    On failure returns an error description string (never empty, never raises):
        - "transcription not installed" when faster-whisper is missing
        - "no speech detected" when whisper ran but found nothing
        - the warning text for any other failure
    """
    result = transcribe_audio(video_path, emit)

    # Whisper not installed.
    install_warns = [w for w in result.warnings if "not installed" in w.lower()]
    if install_warns:
        return "transcription not installed"

    # No audio stream in file (video-only container).
    no_audio_warns = [w for w in result.warnings if "no audio stream" in w.lower()]
    if no_audio_warns:
        return "no audio stream in file"

    # Transcription failed entirely (load error, CUDA failure, etc.).
    fail_warns = [w for w in result.warnings if "failed" in w.lower()]
    if fail_warns and not result.text.strip():
        return "; ".join(result.warnings)

    # No speech detected.
    if not result.text.strip():
        return "no speech detected"

    # Success — format with header.
    lang_tag = f" ({result.language})" if result.language else ""
    header = f"Transcript: whisper {result.model} (local){lang_tag}"
    return f"{header}\n\n{result.text}"