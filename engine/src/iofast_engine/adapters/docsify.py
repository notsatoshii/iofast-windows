"""Docsify. The shell renders nothing server-side — the SPA fetches raw .md files.

Every docsify site serves one `index.html` for every route (hash routes like
`#/quickstart` never reach the server), so a crawl sees exactly one page whose
body is `<div id="app"></div>`. The markdown the SPA renders is plain-fetchable:
`_sidebar.md` is the nav, and each entry is a real `.md` file next to it. This
adapter reads those files and skips the shell entirely.

Measured on four live sites (2026-08-14): docsify.js.org (v5, README.md
homepage), adrianbj.github.io/TracyDebugger (v4 nightly, `homepage:
'introduction.md'`), libocca.org (v4, root `_sidebar.md` empty — homepage only),
adrien2p.github.io/medusa-extender (v4, single-page site whose sidebar is
anchor-only). All four answer `text/markdown` on their root `README.md`.
"""

import re
from urllib.parse import urljoin, urlparse

from .base import Adapter, Doc
from .detection import has

# Markers: the runtime config object plus a script that loads docsify itself.
# Both are needed — a page that merely *documents* `window.$docsify` in prose
# would match the config alone. Config: docsify.js.org:104, TracyDebugger:82,
# libocca.org:41, medusa-extender:38. Script src: `npm/docsify@5/dist/docsify.min.js`,
# `gh/sy-records/docsify-nightly/lib/docsify.min.js`, `unpkg.com/docsify@4/lib/…`,
# `npm/docsify@4` (no filename) respectively.
SCRIPT_RE = re.compile(r"<script[^>]{0,300}?src=[^>]{0,300}?docsify", re.I)

# Sidebar entries: markdown links to a .md file. Anchors (#foo), absolute URLs and
# protocol-relative URLs are skipped here — off-site content is never a page. The
# lookahead after `.md` keeps `guide.mdx` from being truncated into `guide.md`,
# which would fetch a URL that never existed.
LINK_RE = re.compile(
    r"\[[^\]]{0,300}?\]\(\s*(?!https?:|//|#)([^)\s]{1,300}?\.md)(?=[)\s#?'\"])[^)]{0,200}?\)"
)

HOMEPAGE_RE = re.compile(r"\bhomepage\s*:\s*['\"]([^'\"]{1,200})['\"]")
BASEPATH_RE = re.compile(r"\bbasePath\s*:\s*['\"]([^'\"]{1,200})['\"]")
ALIAS_RE = re.compile(r"\balias\s*:\s*\{(.{0,4000}?)\}", re.S)
ALIAS_PAIR_RE = re.compile(r"['\"]([^'\"]{1,200})['\"]\s*:\s*['\"]([^'\"]{1,400})['\"]")
COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:'\"])//[^\n]*", re.S)
CONFIG_RE = re.compile(r"window\.\$docsify\s*=\s*\{(.{0,20000})", re.S)

SIDEBAR = "_sidebar.md"
DEFAULT_HOMEPAGE = "README.md"


def detect(fp):
    return fp.status == 200 and has(fp, "window.$docsify") and SCRIPT_RE.search(fp.html) is not None


def _dir_url(url):
    """The directory a docsify shell is served from: `/repo/index.html` -> `/repo/`.

    Only a real shell filename is stripped. A dotted *directory* is a normal path
    segment — `https://mrdoob.github.io/three.js` is the project root, not a file,
    and stripping it would move the whole run to the wrong site.

    Hash routes carry no path (`/#/quickstart` parses to path `/`), so a deep link
    into a docsify site resolves to the same root as the landing URL.
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path.rsplit("/", 1)[-1].lower().endswith((".html", ".htm")):
        path = path.rsplit("/", 1)[0] + "/"
    elif not path.endswith("/"):
        path += "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _text(response):
    from ..discovery import _body_text

    return _body_text(response) if response is not None else ""


def _config(shell):
    """The `window.$docsify = { … }` body with JS comments removed.

    docsify.js.org's own shell keeps half its options commented out
    (`// repo:`, `// routerMode:`, `// basePath:` is the same shape) — matching
    those would point the whole run at a path the site does not serve. Protocol-
    relative URLs inside quotes are left alone: `'//evil.example.com/'` has to
    survive stripping so the containment check below can reject it.
    """
    found = CONFIG_RE.search(shell or "")
    return COMMENT_RE.sub("", found.group(1)) if found else ""


def _in_root(url, root):
    """Same host, at or below the docsify root, no encoded traversal.

    The shell is scraped content, so every URL it names is untrusted input: a
    `basePath` or `homepage` of `//other.host/` or `../../` would otherwise send
    the fetches (and the corpus written to disk) somewhere else entirely. `%2e`
    and `%2f` are rejected outright — some servers decode them before resolving
    the path, so `startswith` alone would not hold.
    """
    if "%2e" in url.lower() or "%2f" in url.lower():
        return False
    return urlparse(url).netloc == urlparse(root).netloc and url.startswith(root)


def _aliased_off_site(config, target, base):
    """True when docsify's `alias` maps this route somewhere this run cannot follow.

    docsify.js.org aliases `.*?/awesome` and `.*?/changelog` to raw.githubusercontent
    URLs, so `awesome.md` and `changelog.md` genuinely 404 on the site (verified
    2026-08-14). Fetching them would file two failure rows — and a non-zero exit —
    for a perfectly healthy site. The aliased content is off-site, so the entry is
    not a page of this run at all.
    """
    found = ALIAS_RE.search(config)
    if not found:
        return False
    route = "/" + target[len(base):]
    for pattern, destination in ALIAS_PAIR_RE.findall(found.group(1)):
        if not destination.startswith(("http://", "https://", "//")):
            continue  # a local alias still resolves under the root — fetch it as-is
        for candidate in (route, route[:-3] if route.endswith(".md") else route):
            try:
                if re.fullmatch(pattern, candidate):
                    return True
            except re.error:
                return False
    return False


def _targets(shell, sidebar, root):
    """Homepage first, then every in-scope sidebar entry, deduped, order kept."""
    config = _config(shell)
    base = root
    found = BASEPATH_RE.search(config)
    if found:
        candidate = _dir_url(urljoin(root, found.group(1)))
        base = candidate if _in_root(candidate, root) else root
    found = HOMEPAGE_RE.search(config)
    home = urljoin(base, found.group(1)) if found else ""
    targets = [home if _in_root(home, base) else urljoin(base, DEFAULT_HOMEPAGE)]
    for href in LINK_RE.findall(sidebar):
        target = urljoin(base, href)
        if _in_root(target, base) and target not in targets:
            if not _aliased_off_site(config, target, base):
                targets.append(target)
    return targets


def harvest(ctx):
    """Two setup fetches (the shell for its config, then `_sidebar.md`), one per page."""
    from ..discovery import looks_like_markdown
    from ..tiers import THIN_CHARS

    root = _dir_url(ctx.scope)
    shell = _text(ctx.get(root))
    sidebar_url = urljoin(root, SIDEBAR)
    response = ctx.get(sidebar_url)
    sidebar = _text(response)
    status = int(getattr(response, "status", 0) or 0)
    if status != 200:
        # A blocked or missing nav and a deliberately empty one both yield no
        # entries — say which, so a one-page run is never mistaken for a small site.
        ctx.emit(
            {
                "type": "warning",
                "url": sidebar_url,
                "message": f"⚠️  no sidebar (http {status}): homepage only",
            }
        )
    for url in _targets(shell, sidebar, root):
        ctx.ledger.enqueue(url)  # enqueue before the fetch — a failure stays visible
        response = ctx.get(url)
        status = int(getattr(response, "status", 0) or 0)
        headers = dict(getattr(response, "headers", None) or {})
        text = _text(response)
        if looks_like_markdown(status, headers, text):
            yield Doc(url=url, markdown=text)
            continue
        if status != 200:
            reason = f"http {status}"
        elif not text.lstrip().startswith("<") and len(text.strip()) < THIN_CHARS:
            reason = f"thin ({len(text.strip())} chars)"
        else:
            reason = "not markdown"
        ctx.ledger.record(url, "failed_fetch", reason, tier="api", status=status)
        ctx.emit({"type": "warning", "url": url, "message": f"⚠️  {reason}: {url}"})


ADAPTER = Adapter(
    name="docsify",
    detect=detect,
    fetch_mode="api",
    harvest=harvest,
)
