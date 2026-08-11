"""Notion public pages. Known fail — scraping them produces empty or partial output."""

from .base import Adapter
from .detection import has, host_is

# Markers: <html class="notion-html" data-notion-html="web" data-notion-version="…">
# tests/fixtures/platforms/notion/static-theiagen.html + static-citybld.html (and both
# rendered-*.html twins). Host check covers *.notion.site — the fixture URLs are
# theiagen.notion.site and worldbld.notion.site.
#
# Why known_fail: static-theiagen.html extracts 89 chars, and even the *rendered* DOM
# only reaches 594 for one page and stays at 89 for the other (rendered-citybld.html).
ADAPTER = Adapter(
    name="notion",
    detect=lambda fp: host_is(fp, "notion.site", "notion.so")
    or has(fp, 'class="notion-html"', "data-notion-html"),
    known_fail=(
        "Notion renders blocks lazily with no stable selectors; scraping produces "
        "empty or partial output. Export the page from Notion instead."
    ),
)
