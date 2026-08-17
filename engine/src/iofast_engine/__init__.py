"""iofast-engine: crawl a documentation site into hyphen-named markdown files."""

import sys

__version__ = "0.1.0"

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
