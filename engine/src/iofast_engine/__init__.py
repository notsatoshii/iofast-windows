"""iofast-engine: crawl a documentation site into hyphen-named markdown files."""

import sys

__version__ = "0.1.0"

# Build provenance: iofast.spec writes _build.py carrying the git SHA at freeze
# time. Unfrozen runs always report "dev" — a stale _build.py left behind by a
# past freeze must never masquerade as a stamped build.
if getattr(sys, "frozen", False):
    try:
        from ._build import BUILD_SHA as __build__
    except ImportError:
        __build__ = "unknown"
else:
    __build__ = "dev"
