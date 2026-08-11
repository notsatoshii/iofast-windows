"""Fingerprint helpers + the one static sample taken before a crawl starts.

detect() rules stay cheap on purpose: substring/regex over the head of the
document, the response headers, or the URL. No DOM parsing.
"""

import re
from urllib.parse import urlparse

from ..tiers import is_challenge
from .base import Fingerprint

HEAD = 100_000  # detect() only ever reads the head of the document


def fingerprint(url: str, status: int, headers: dict, html: str) -> Fingerprint:
    """Fingerprint with lowercased header keys and the body capped at HEAD."""
    return Fingerprint(
        url=url,
        status=int(status or 0),
        headers={str(k).lower(): v for k, v in (headers or {}).items()},
        html=(html or "")[:HEAD],
    )


def has(fp: Fingerprint, *markers: str) -> bool:
    """Case-insensitive substring test over the head of the document."""
    head = fp.html[:HEAD].lower()
    return any(marker.lower() in head for marker in markers)


def generator(fp: Fingerprint, name: str) -> bool:
    """<meta name="generator" content="Name…"> — quoting varies between platforms.

    Docusaurus emits unquoted attributes (`name=generator content="Docusaurus v3.10.1"`,
    tests/fixtures/platforms/docusaurus/static-landing.html), GitBook/Mintlify quote them.
    """
    pattern = r'<meta[^>]{0,200}?name=["\']?generator["\']?[^>]{0,200}?content=["\']?' + re.escape(name)
    return re.search(pattern, fp.html[:HEAD], re.I) is not None


def host_is(fp: Fingerprint, *suffixes: str) -> bool:
    """Exact host or any subdomain of it."""
    host = urlparse(fp.url).netloc.lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def detect_late(url, status, headers, html, emit=None):
    """Re-detection on a crawled page: only 200s that are not a challenge count."""
    fp = fingerprint(url, status, headers, html)
    if fp.status != 200 or is_challenge(fp.status, fp.headers, fp.html):
        return None
    from . import detect_platform

    return detect_platform(fp, emit=emit)


def selectors_for(adapter, generic):
    """The adapter's content selector wins; the generic list stays as the fallback."""
    if adapter is None or not adapter.content_selector:
        return tuple(generic)
    return (adapter.content_selector,) + tuple(generic)


def preflight(start_url, emit=None):
    """Resolve the start URL, then ONE static GET to fingerprint it.

    Returns (resolved_url, adapter or None). A challenge or non-200 sample leaves
    the platform undetected — the spider then re-detects on each crawled page.
    """
    emit = emit or (lambda event: None)
    from ..discovery import _body_text, _get, resolve_start

    resolved = resolve_start(start_url, emit=emit)
    response = _get(resolved)
    adapter = None
    if response is not None:
        adapter = detect_late(
            resolved,
            getattr(response, "status", 0),
            dict(getattr(response, "headers", None) or {}),
            _body_text(response),
            emit=emit,
        )
    emit(
        {
            "type": "platform",
            "platform": adapter.name if adapter else None,
            "message": (
                f"🧩 platform: {adapter.name}" if adapter else "🧩 platform: none detected"
            ),
        }
    )
    return resolved, adapter
