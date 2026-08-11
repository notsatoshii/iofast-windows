"""Read the Docs. Sphinx output plus the Read the Docs addons."""

from .base import Adapter
from .detection import has

# Markers: the addons handshake tag and the project slug tag Read the Docs injects:
#   <meta name="readthedocs-addons-api-version" content="1">
#   <meta name="readthedocs-project-slug" content="docs">
# tests/fixtures/platforms/readthedocs/static-landing.html + static-tutorial.html.
ADAPTER = Adapter(
    name="readthedocs",
    detect=lambda fp: has(
        fp, 'name="readthedocs-addons-api-version"', 'name="readthedocs-project-slug"'
    ),
    fetch_strategy="static",  # static-tutorial.html extracts 19638 chars
    content_selector=None,  # div[role="main"] is in CONTENT_SELECTORS already
    nav_selector="nav.wy-nav-side",  # sphinx_rtd_theme sidebar — static-landing.html
)
