"""Source registry: detect(url) -> Source for youtube/github/instagram/x/web."""

from .base import GrabOptions, GrabResult, Source


def _lazy_instagram():
    from .instagram import InstagramSource
    return InstagramSource()


def _lazy_x():
    from .x import XSource
    return XSource()


# Registry order matters: more specific matchers first, web last.
_SOURCES = [
    ("instagram", _lazy_instagram),
    ("x", _lazy_x),
]


def detect(url: str) -> Source | None:
    """Return the first source whose matcher accepts the URL, or None."""
    for _name, factory in _SOURCES:
        src = factory()
        if src.matches(url):
            return src
    return None


__all__ = ["GrabOptions", "GrabResult", "Source", "detect"]