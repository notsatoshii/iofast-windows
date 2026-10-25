"""iofast-engine: crawl a documentation site into hyphen-named markdown files."""

import sys
from pathlib import Path


def _version() -> str:
    """Engine version. The repo-root VERSION file is the single source.

    Frozen: iofast.spec stamps it into _build.py. Source checkout: read VERSION
    directly, so a bump shows without reinstalling. Installed elsewhere: the
    package metadata (pyproject carries the same number).
    """
    if getattr(sys, "frozen", False):
        try:
            from ._build import VERSION

            return VERSION
        except ImportError:
            return "unknown"
    root = Path(__file__).resolve().parents[3]
    if (root / "engine" / "pyproject.toml").is_file():
        try:
            return (root / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    try:
        from importlib.metadata import version

        return version("iofast-engine")
    except Exception:
        return "unknown"


__version__ = _version()

# Build provenance: iofast.spec writes _build.py carrying the SHA of the last
# commit that changed the engine sources, read at freeze time (the app bakes in
# the same value and compares on handshake — see app/src-tauri/build.rs).
# Unfrozen runs always report "dev" — a stale _build.py left behind by a
# past freeze must never masquerade as a stamped build.
if getattr(sys, "frozen", False):
    try:
        from ._build import BUILD_SHA as __build__
    except ImportError:
        __build__ = "unknown"
else:
    __build__ = "dev"
