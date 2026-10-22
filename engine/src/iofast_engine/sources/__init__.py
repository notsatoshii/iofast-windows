"""Source registry. detect(url) returns the first matching source; WebSource
is always last so it acts as the universal fallback when nothing else claims
the URL. Every source module is loaded best-effort: workers land them on
separate branches, and the registry simply skips whatever isn't present yet.
Once all five source modules exist, the registry is fully populated."""

from .base import GrabOptions, GrabResult, Source

# --- source modules (each lands on its own branch; missing = skip) --------
try:
    from .youtube import YouTubeSource
except ImportError:
    YouTubeSource = None  # worker will add youtube.py

try:
    from .github import GitHubSource
except ImportError:
    GitHubSource = None  # worker will add github.py

try:
    from .instagram import InstagramSource
except ImportError:
    InstagramSource = None  # worker will add instagram.py

try:
    from .x import XSource
except ImportError:
    XSource = None  # worker will add x.py

try:
    from .web import WebSource
except ImportError:
    WebSource = None  # worker will add web.py

# Registry order matters: first match wins. Web is always last (fallback).
# Each slot is only populated when its module exists; the list compacts
# automatically so detect() never sees a None entry.
_REGISTRY: list[Source] = [
    *([YouTubeSource()] if YouTubeSource is not None else []),
    *([GitHubSource()] if GitHubSource is not None else []),
    *([InstagramSource()] if InstagramSource is not None else []),
    *([XSource()] if XSource is not None else []),
    *([WebSource()] if WebSource is not None else []),
]


def detect(url: str) -> Source:
    """Return the first source whose matches(url) is True. Falls back to
    WebSource when nothing else claims the URL — web scraping is the safe
    default for any URL shape we don't recognise yet. Raises RuntimeError
    if no source modules have landed yet (shouldn't happen in production)."""
    for source in _REGISTRY:
        if source.matches(url):
            return source
    if _REGISTRY:
        return _REGISTRY[-1]
    raise RuntimeError("no source modules available — run the source workers first")


__all__ = ["GrabOptions", "GrabResult", "Source", "detect"]