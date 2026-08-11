"""Discovery: sitemap.xml -> llms.txt -> link crawl, plus robots.txt.

Module level stays free of scrapling so the pure parsers import offline; the
fetcher is imported lazily inside _get().
"""

import re
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

# Untrusted input: a sitemap or llms.txt is attacker-shaped data, not a trusted feed.
MAX_BODY = 10 * 1024 * 1024
MAX_SITEMAP_URLS = 50_000

MIN_INTERVAL = 0.5  # seconds between calls to the same host
_last_call: dict[str, float] = {}
# ponytail: one global lock held across the politeness sleep — a concurrent
# map + scrape (RPC) stay spaced; per-host locks only if cross-host fan-out appears.
_last_call_lock = threading.Lock()

USER_AGENT = "iofast-docs-scraper"
ROBOTS_POLICY = (
    f"obey robots.txt Disallow for '{USER_AGENT}' and '*' (log and obey; "
    "unreachable robots.txt = allow)"
)


def _get(url: str, timeout: int = 20, headers: dict | None = None):
    """Static GET that never raises. Returns the Response or None.

    Politeness: calls to the same host are spaced at least MIN_INTERVAL apart.
    `headers` carries API auth (a Bearer token read from the environment, never argv).
    """
    from scrapling.fetchers import Fetcher  # lazy: keeps this module importable offline

    host = urlparse(url).netloc
    with _last_call_lock:  # held across the sleep so concurrent callers stay spaced
        last = _last_call.get(host)
        if last is not None:
            wait = MIN_INTERVAL - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        _last_call[host] = time.monotonic()
    try:
        extra = {"headers": headers} if headers else {}
        return Fetcher.get(url, timeout=timeout, stealthy_headers=True, **extra)
    except Exception:
        return None


def _body_text(response) -> str:
    """Decoded body, capped at MAX_BODY raw AND after gunzip (decompression bomb guard)."""
    body = response.body
    if len(body) > MAX_BODY:
        return ""
    if body[:2] == b"\x1f\x8b":
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, MAX_BODY + 1)
        except Exception:
            return ""
        if len(body) > MAX_BODY:
            return ""
    return body.decode("utf-8", errors="ignore")


def _tag(elem) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def parse_sitemap(xml_text: str):
    """Return (page_urls_with_lastmod, nested_sitemap_urls)."""
    pages, nested = [], []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return pages, nested

    is_index = _tag(root) == "sitemapindex"
    for entry in root:
        loc = lastmod = None
        for child in entry:
            if _tag(child) == "loc":
                loc = (child.text or "").strip()
            elif _tag(child) == "lastmod":
                lastmod = (child.text or "").strip()
        if not loc:
            continue
        if is_index or _tag(entry) == "sitemap":
            nested.append(loc)
        else:
            pages.append((loc, lastmod))
    return pages, nested


def parse_llms(text: str, base: str):
    """Absolute links out of an llms.txt file (markdown link list)."""
    urls = []
    for raw in re.findall(r"\]\(([^)\s]+)\)", text):
        urls.append(urljoin(base, raw))
    for raw in re.findall(r"(?m)^\s*<?(https?://\S+?)>?\s*$", text):
        urls.append(raw)
    return urls


def discover_sitemap(origin: str, extra_urls=(), max_sitemaps: int = 25, emit=None):
    """Walk sitemap.xml (recursing indexes, handling .xml.gz). Returns [(url, lastmod)]."""
    emit = emit or (lambda event: None)
    queue = list(extra_urls) + [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
    seen, pages = set(), []
    while queue and len(seen) < max_sitemaps:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        response = _get(url)
        if not response or response.status != 200:
            continue
        text = _body_text(response)
        if "<!doctype" in text[:4096].lower():
            # An XML parser must never be handed a DOCTYPE: entity expansion.
            emit({"type": "warning", "message": f"⚠️  skipped sitemap with DOCTYPE: {url}"})
            continue
        found, nested = parse_sitemap(text)
        pages.extend(found)
        queue.extend(nested)
        if len(pages) >= MAX_SITEMAP_URLS:
            pages = pages[:MAX_SITEMAP_URLS]
            emit(
                {
                    "type": "warning",
                    "message": f"⚠️  sitemap truncated at {MAX_SITEMAP_URLS} urls",
                }
            )
            break
    return pages


def robots_sitemaps(origin: str):
    response = _get(f"{origin}/robots.txt")
    if not response or response.status != 200:
        return []
    return re.findall(r"(?im)^\s*sitemap:\s*(\S+)\s*$", _body_text(response))


class Robots:
    """robots.txt gate, one parse per origin (stdlib urllib.robotparser)."""

    def __init__(self):
        self._cache = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._cache:
            parser = RobotFileParser()
            response = _get(f"{origin}/robots.txt")
            if response and response.status == 200:
                parser.parse(_body_text(response).splitlines())
            else:
                parser = None  # unreachable robots.txt: nothing to obey
            self._cache[origin] = parser
        parser = self._cache[origin]
        if parser is None:
            return True
        return parser.can_fetch(USER_AGENT, url) and parser.can_fetch("*", url)


def resolve_start(start_url: str, emit=None) -> str:
    """Follow the start URL's redirect: adopt the new protocol+host.

    The redirect's final path wins whenever it is non-root: the server mapped our
    URL somewhere specific and that URL demonstrably resolves (docs.readthedocs.io
    /en/stable/ -> docs.readthedocs.com/platform/en/stable/ — synthesizing the old
    path onto the new host 404s). The user's path is restored only when the
    redirect dumps to the root, where their scope intent would otherwise be lost.
    """
    emit = emit or (lambda event: None)
    response = _get(start_url)
    final = getattr(response, "url", None)
    if not final:
        return start_url
    new, old = urlparse(final), urlparse(start_url)
    if not new.netloc or new.netloc == old.netloc:
        return start_url
    user_path = old.path if old.path and old.path != "/" else ""
    final_path = new.path if new.path and new.path != "/" else ""
    path = final_path or user_path or "/"
    emit(
        {
            "type": "progress",
            "message": (
                f"↪️  redirect: {old.netloc} -> {new.netloc} (scope path: {path or '/'})"
            ),
        }
    )
    return f"{new.scheme}://{new.netloc}{path}"


def discover_llms(origin: str, scope_root: str):
    """Try llms.txt / llms-full.txt at the origin and at the scope root."""
    bases = [origin, scope_root.rstrip("/")]
    for base in dict.fromkeys(bases):
        for name in ("llms-full.txt", "llms.txt"):
            url = f"{base}/{name}"
            response = _get(url)
            if response and response.status == 200:
                urls = parse_llms(_body_text(response), url)
                if urls:
                    return url, urls
    return None, []
