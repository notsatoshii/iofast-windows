"""NVIDIA runtime DLLs for GPU whisper on Windows, fetched once on demand.

ctranslate2's Windows wheel ships only a cuDNN loader stub; cuBLAS and the
cuDNN sub-libraries are needed to run on the GPU. They are ~1.3 GB, so they
are not bundled: the first transcription on a machine with an NVIDIA GPU
downloads two pinned PyPI wheels, checks their sha256, and keeps only the
DLLs in %LOCALAPPDATA%/iofast/cuda. Any failure means CPU, never an error.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

# (url, sha256) of the pinned wheels. cuBLAS 12 + cuDNN 9 match ctranslate2 4.x.
WHEELS = [
    (
        "https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
        "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
    ),
    (
        "https://files.pythonhosted.org/packages/aa/38/f856579877f7c1c5066e61182e7de7bc27bf35a78c8d1b0fa592e6985bc4/nvidia_cudnn_cu12-9.27.0.42-py3-none-win_amd64.whl",
        "06e9b0026f3bad97d2b58666330fabec04fe1672f776661ecb0ce0029c27f142",
    ),
]
REQUIRED = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")


def libs_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "iofast" / "cuda"


def has_gpu() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _fetch(url: str, sha256: str, dest: Path, emit: Callable[[dict], None]) -> None:
    """Download one wheel and extract its DLLs into dest. Raises on mismatch."""
    with tempfile.TemporaryDirectory() as tmp:
        whl = Path(tmp) / "w.whl"
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as resp, open(whl, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = last = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else 0
                if pct >= last + 10:
                    last = pct
                    emit({"type": "progress", "message": f"🎮 GPU libraries {pct}%"})
        if digest.hexdigest() != sha256:
            raise ValueError("checksum mismatch")
        with zipfile.ZipFile(whl) as z:
            for name in z.namelist():
                if name.endswith(".dll"):
                    part = dest / (Path(name).name + ".part")
                    part.write_bytes(z.read(name))
                    os.replace(part, dest / Path(name).name)


def ensure(emit: Callable[[dict], None]) -> bool:
    """True when the GPU can be used: libs present (or just fetched) and on the
    DLL search path. Windows only; elsewhere the system CUDA install decides."""
    if sys.platform != "win32":
        return has_gpu()
    if not has_gpu():
        return False
    dest = libs_dir()
    try:
        if not all((dest / n).is_file() for n in REQUIRED):
            dest.mkdir(parents=True, exist_ok=True)
            emit({"type": "progress", "message": "🎮 one-time download of GPU libraries (~1.3 GB)"})
            for url, sha in WHEELS:
                _fetch(url, sha, dest, emit)
        os.add_dll_directory(str(dest))
        os.environ["PATH"] = str(dest) + os.pathsep + os.environ.get("PATH", "")
        return True
    except Exception as exc:
        emit({"type": "warning", "message": f"GPU libraries unavailable ({exc}); using CPU"})
        return False
