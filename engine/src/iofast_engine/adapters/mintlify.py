"""Mintlify. SSR'd: static and rendered extract identically."""

from .base import Adapter
from .detection import generator

# Marker: <meta name="generator" content="Mintlify"/>
# tests/fixtures/platforms/mintlify/static-landing.html + static-quickstart.html
#   (and rendered-quickstart.html).
ADAPTER = Adapter(
    name="mintlify",
    detect=lambda fp: generator(fp, "Mintlify"),
    # static-quickstart.html and rendered-quickstart.html both extract 1481 chars.
    fetch_strategy="static",
    content_selector=None,  # <main> is present on article pages
    # <nav id="sidebar-content" aria-label="Pages"> — static-quickstart.html
    nav_selector="#sidebar-content",
)
