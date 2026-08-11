"""ReadMe (readme.com). The static HTML is a shell — the body arrives from JS."""

from .base import Adapter
from .detection import has

# Markers: <meta name="readme-deploy" content="5.841.0"> and
# <meta name="readme-subdomain" content="developers">
# tests/fixtures/platforms/readme/static-landing.html + static-getting-started.html.
#
# The 404 negative (tests/fixtures/negatives/notfound.html) is itself a ReadMe page
# and carries the same two markers — the status gate is what keeps it out. Detection
# only ever runs on a 200 anyway (see detection.detect_late).
ADAPTER = Adapter(
    name="readme",
    detect=lambda fp: fp.status == 200 and has(fp, 'name="readme-deploy"', 'name="readme-subdomain"'),
    # static-getting-started.html extracts 44 chars, rendered-getting-started.html 926.
    fetch_strategy="dynamic",
    content_selector=None,  # <main> and <article> are present once rendered
    # <nav aria-label="Secondary navigation" id="hub-sidebar"> — static-landing.html
    nav_selector='nav[aria-label="Secondary navigation"]',
)
