"""Redocly (Realm/Revel/Reef). SSR'd shell + a per-route data.json."""

from .base import Adapter
from .detection import has

# Markers: the route-change hook Redocly injects and its preloaded page data —
#   window.onRouteChange = (callback) => {…}
#   <link rel="preload" as="fetch" … href="/app-data.json" />
#   <link rel="preload" as="fetch" … href="/page-data/docs/data.json" />
# tests/fixtures/platforms/redocly/static-landing.html + static-cli-installation.html.
ADAPTER = Adapter(
    name="redocly",
    detect=lambda fp: has(fp, "window.onRouteChange") and has(fp, 'href="/app-data.json"'),
    fetch_strategy="static",  # static-cli-installation.html extracts 3396 chars
    content_selector=None,  # <main> and <article> are both present
    nav_selector=None,  # the fixture's navbar carries only hashed styled-components classes
)
