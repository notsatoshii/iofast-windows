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


def _past_leading_comments(text: str) -> str:
    """The body with any leading HTML comments removed.

    A markdown file may legitimately open with one — docsify.js.org's ui-kit.md
    and its own `_sidebar.md` both start `<!-- markdownlint-disable … -->`, and a
    naive "starts with <" test files 17KB of real markdown as not-markdown. An
    HTML document that hides behind a comment (`<!-- saved from … --><html>`)
    still fails the test after the comments come off.
    """
    body = text.lstrip()
    while body.startswith("<!--"):
        end = body.find("-->")
        if end == -1:
            break
        body = body[end + 3 :].lstrip()
    return body


def looks_like_markdown(status: int, headers: dict, text: str) -> bool:
    """Shared md-twin accept gate: status, markdown-ish content type, non-HTML
    body, and the THIN_CHARS floor so a tiny text/plain soft-404 never passes.

    THE one definition — probe_md_twin (run-level convention proof) and the
    spider's per-page md_probe branch must never drift apart on these criteria.
    """
    from .tiers import THIN_CHARS, header_lookup

    ctype = header_lookup(headers or {}, "content-type").lower()
    return (
        status == 200
        and ("markdown" in ctype or "text/plain" in ctype)
        and not _past_leading_comments(text).startswith("<")
        and len(text.strip()) >= THIN_CHARS
    )


def probe_md_twin(url: str) -> bool:
    """One GET proving a `.md` twin serves real markdown.

    ReadMe-style llms.txt files index PLAIN page urls while the site still
    serves `<page>.md` twins — the twin convention must be proven, not assumed
    from the llms.txt link shape.
    """
    response = _get(url)
    if not response:
        return False
    return looks_like_markdown(
        response.status, dict(response.headers or {}), _body_text(response)
    )


# A root llms.txt can be an index OF llms.txt files (ReadMe nests root ->
# section -> page links). One expansion level reaches the leaves there; the cap
# bounds the fetch fan-out on a hostile or enormous index. Measured 2026-08-13:
# docs.readme.com lists 29 nested indexes today — 50 leaves headroom, and the
# truncation warns instead of silently dropping sections.
MAX_NESTED_LLMS = 50


def _is_llms_index(url: str) -> bool:
    path = urlparse(url).path
    return path.endswith("/llms.txt") or path.endswith("/llms-full.txt")


# A link INDEX is a bullet list; a content BUNDLE is prose that merely contains
# links. Measured 2026-08-13 — indexes: docs.readme.com leaf 0.444, mid 0.700,
# developers.cloudflare.com leaf 0.308; bundles: svelte.dev docs/{svelte,kit,ai}
# 0.003-0.007. Two orders of magnitude apart; 0.1 sits well clear of both ends.
# Bulleted or numbered list entries both count as index lines.
_INDEX_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s*\[[^\]]*\]\((\S+?)\)")
INDEX_MIN_BULLET_RATIO = 0.1


def _index_links(text: str, base: str):
    """Bullet-list links, or [] when the file reads as a content bundle.

    Only bullet LINES count as index entries — a bundle's prose links (external
    `.md` files, relative content refs) must never become crawl seeds
    (measured 2026-08-13: expanding svelte's bundles seeded 404 pages).
    """
    lines = [line for line in text.splitlines() if line.strip()]
    hits = [m.group(1) for line in lines if (m := _INDEX_BULLET_RE.match(line))]
    if not lines or len(hits) / len(lines) < INDEX_MIN_BULLET_RATIO:
        return []
    return [urljoin(base, h) for h in hits]


def _expand_nested_llms(urls, emit=None, host=None):
    """Replace nested llms.txt links with the page links they index (depth 1).

    A nested llms.txt is either a link INDEX (ReadMe root -> section files,
    Cloudflare directory -> product files) whose bullet links are the real page
    list, or a content BUNDLE (svelte's docs/*/llms.txt) that must be skipped
    wholesale. With nothing collected, the original list is returned untouched
    and the crawl keeps its baseline behavior (md off, link crawl).
    """
    from .urls import norm_host

    emit = emit or (lambda event: None)
    pages = [u for u in urls if not _is_llms_index(u)]
    # Same-host only: an llms.txt is untrusted input, and following its nested
    # index links to arbitrary hosts would let one file aim up to 50 GETs
    # anywhere (including link-local endpoints) while defeating the per-host
    # politeness spacing. Every legitimate nested shape measured (ReadMe,
    # Cloudflare) stays on its own host.
    nested = [
        u
        for u in urls
        if _is_llms_index(u) and host and norm_host(urlparse(u).netloc) == norm_host(host)
    ]
    if any(p.endswith(".md") for p in pages):
        # Flat Mintlify-style list already proves the twin convention with
        # direct `.md` page links; a bulleted llms-full.txt beside them is a
        # bundle, and fetching multi-MB bundles just to reject them by ratio
        # would tax the healthiest path on every discovery.
        return urls
    if nested:
        # Same-host fetches are politeness-spaced, so a big index takes seconds
        # inside spider construction — say so instead of looking hung.
        emit(
            {
                "type": "progress",
                "message": f"📚 expanding {min(len(nested), MAX_NESTED_LLMS)} nested llms.txt indexes",
            }
        )
    if len(nested) > MAX_NESTED_LLMS:
        emit(
            {
                "type": "warning",
                "message": (
                    f"⚠️  llms.txt lists {len(nested)} nested indexes — "
                    f"expanding only the first {MAX_NESTED_LLMS}"
                ),
            }
        )
    expanded = []
    barren = 0
    for url in nested[:MAX_NESTED_LLMS]:
        response = _get(url)
        links = (
            _index_links(_body_text(response), url)
            if response and response.status == 200
            else []
        )
        links = [link for link in links if not _is_llms_index(link)]
        if links:
            expanded.extend(links)
        else:
            barren += 1
    if expanded:
        if barren:
            # A partially-expanded frontier silently loses whole sections —
            # say which fraction failed to contribute (fetch error, or a
            # section that reads as a content bundle).
            emit(
                {
                    "type": "warning",
                    "message": (
                        f"⚠️  {barren} of {min(len(nested), MAX_NESTED_LLMS)} "
                        "nested llms.txt indexes contributed no links"
                    ),
                }
            )
        return list(dict.fromkeys(pages + expanded))
    return urls


def discover_llms(origin: str, scope_root: str, emit=None):
    """Try llms.txt / llms-full.txt at the origin and at the scope root.

    `llms.txt` (the curated link index) comes first: it lists the per-page
    `.md` twins the scraper wants. `llms-full.txt` is the whole docs
    concatenated into one file — its embedded links are content references
    (often external), not a clean page index — so it is only a fallback.
    """
    bases = [origin, scope_root.rstrip("/")]
    for base in dict.fromkeys(bases):
        for name in ("llms.txt", "llms-full.txt"):
            url = f"{base}/{name}"
            response = _get(url)
            if response and response.status == 200:
                urls = parse_llms(_body_text(response), url)
                if urls:
                    return url, _expand_nested_llms(
                        urls, emit=emit, host=urlparse(url).netloc
                    )
    return None, []
