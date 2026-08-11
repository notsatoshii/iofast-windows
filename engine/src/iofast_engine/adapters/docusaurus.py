"""Docusaurus. Fully static site generator."""

from .base import Adapter
from .detection import generator

# Marker: <meta name=generator content="Docusaurus v3.10.1"> — attributes unquoted.
# tests/fixtures/platforms/docusaurus/static-landing.html + static-installation.html
ADAPTER = Adapter(
    name="docusaurus",
    detect=lambda fp: generator(fp, "Docusaurus"),
    fetch_strategy="static",  # static-installation.html extracts 8888 chars
    content_selector=None,  # <main> and <article> are both present
    # <nav aria-label="Docs sidebar" class="menu …"> — static-installation.html
    nav_selector='nav[aria-label="Docs sidebar"]',
)
