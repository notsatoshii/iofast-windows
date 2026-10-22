"""Live smoke test for the sources package.

Runs against real public endpoints — NOT part of pytest (offline only there).
Usage: engine/.venv/Scripts/python engine/tools/sources_smoke.py

Produces three run folders under a temp directory and prints their paths.
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure the engine package is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iofast_engine.sources import GrabOptions, detect


TARGETS = [
    ("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("github", "https://github.com/pypa/sampleproject"),
    ("web", "https://example.com"),
]


def emit(event):
    msg = event.get("message")
    if msg:
        print(f"  {msg}", flush=True)


async def run_one(label, url, out_root):
    source = detect(url)
    print(f"\n=== {label}: {url} -> source={source.name} ===")
    opts = GrabOptions(mode="page", lang="en")
    result = await source.grab(url, out_root, opts, emit)
    print(f"  result: ok={result.ok} thin={result.thin} failed={result.failed} dir={result.run_dir}")
    return result


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="iofast-smoke-"))
    print(f"Smoke output: {tmp}")
    results = []
    for label, url in TARGETS:
        out_dir = tmp / label
        out_dir.mkdir()
        r = await run_one(label, url, out_dir)
        results.append((label, r))
    print("\n=== Summary ===")
    for label, r in results:
        status = "✅" if r.ok > 0 and r.failed == 0 else "⚠️"
        print(f"  {status} {label}: ok={r.ok} thin={r.thin} failed={r.failed}")
    print(f"\nRun folders under: {tmp}")


if __name__ == "__main__":
    asyncio.run(main())