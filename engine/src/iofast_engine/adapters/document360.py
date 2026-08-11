"""Document360. Angular knowledge base — the static HTML is a shell."""

from .base import Adapter
from .detection import has

# Markers: the banner comment Document360 ships in every page head, and the
# version attribute on <html>:
#   "This site is powered by Document360 (https://www.document360.com)…"
#   <html … data-project-version-id="e75ab3ac-…" data-version-slug="help">
# tests/fixtures/platforms/document360/static-landing.html + static-getting-started.html.
ADAPTER = Adapter(
    name="document360",
    detect=lambda fp: has(fp, "powered by Document360") or has(fp, "data-project-version-id"),
    # static-getting-started.html extracts 218 chars (the shell), the rendered twin 841.
    fetch_strategy="dynamic",
    content_selector=None,  # the rendered DOM carries <article> and div[role="main"]
    nav_selector=None,  # no <nav> in either the static shell or the rendered capture
)
