"""GitBook. SSR'd: the static HTML already carries the article."""

from .base import Adapter
from .detection import generator

# Marker: <meta name="generator" content="GitBook (634a24f)"/>
# tests/fixtures/platforms/gitbook/static-landing.html + static-blocks.html
#   (and both rendered-*.html twins).
ADAPTER = Adapter(
    name="gitbook",
    detect=lambda fp: generator(fp, "GitBook"),
    fetch_strategy="static",  # static-blocks.html extracts 1999 chars without a browser
    content_selector=None,  # <main> is present, so CONTENT_SELECTORS already hits it
    nav_selector=None,  # the fixture's sidebar carries only hashed Tailwind classes
)
