"""URL shaping and scope filtering. Pure stdlib — imports nothing from the fetch stack."""

import re
from urllib.parse import urlparse


def clean_url(url: str) -> str:
    """Drop fragment + query (F9: all params stripped by default) and trailing slash."""
    return url.split("#")[0].split("?")[0].rstrip("/")


def norm_host(host: str) -> str:
    """www.example.com and example.com are the same site (sitemaps mix the two)."""
    return host.lower().removeprefix("www.")


def scope_prefix(url: str) -> str:
    """Docs-path scope anchor: /docs/intro -> /docs/ , /docs/ -> /docs/ , / -> /."""
    path = urlparse(url).path
    if not path or path == "/":
        return "/"
    if path.endswith("/"):
        return path
    return path.rsplit("/", 1)[0] + "/"


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def section_folder(url: str) -> str:
    """Stable folder name for one docs section: <host>/<section>.

    The section is the scope prefix, cut right after the first id-shaped segment, so
    every URL inside one portal API (/catalog/api/<uuid>/doc?page=..., /catalog/api/<uuid>)
    names the same folder while API A and API B stay apart. Offline and deterministic.
    """
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    for i, part in enumerate(parts):
        if UUID.match(part):
            parts = parts[: i + 1] + [""]
            break
    prefix = scope_prefix(parsed._replace(path="/".join(parts)).geturl())
    site = re.sub(r"[^a-z0-9]+", "-", norm_host(parsed.netloc)).strip("-")
    section = re.sub(r"[^A-Za-z0-9]+", "-", prefix).strip("-")
    return f"docs-{site}/{section or '_root'}"


def path_depth(url: str, prefix: str) -> int:
    """Depth = URL path segments beyond the scope prefix (NOT link hops)."""
    path = urlparse(url).path
    # The scope-root page itself is depth 0: /docs == /docs/ (clean_url strips the
    # slash, which used to push the docs index page out of scope entirely).
    if path.rstrip("/") == prefix.rstrip("/"):
        return 0
    if not path.startswith(prefix):
        return -1
    rest = path[len(prefix):].strip("/")
    return len([s for s in rest.split("/") if s])


# Locale directories (i18n duplicates of the same docs), and paths that are never docs.
_LOCALE_CODES = (
    "af ar bg bn ca cs da de el es et fa fi fr he hi hr hu id it ja ko lt lv ms nb "
    "nl no pl pt ro ru sk sl sr sv th tr uk vi zh"
).split()
LOCALE_RE = re.compile(
    r"^(?:" + "|".join(_LOCALE_CODES) + r")(?:[-_][a-zA-Z]{2,4})?$"
)
# Suffix twins: ant.design serves /docs/react/foo-cn beside /docs/react/foo.
# "cn" is the observed suffix convention; the language codes cover -de/-ja/… twins.
SUFFIX_CODES = frozenset(_LOCALE_CODES) | {"cn"}
# For version-root detection (/en/latest): the locale-exclude list PLUS "en" —
# English is deliberately absent from _LOCALE_CODES (it's the canonical language
# we keep, not an i18n duplicate to drop), yet /en/... is the most common
# Read-the-Docs version-root shape.
VERSION_ROOT_LOCALES = frozenset(_LOCALE_CODES) | {"en"}


def suffix_twin(url: str, url_set, keep: frozenset = frozenset()) -> bool:
    """True when url is <base>-<locale> and <base> is also in the discovered set.

    Twin-based on purpose: a blanket "-cn" rule would eat pages genuinely named
    that way. Only kicks in when the un-suffixed sibling actually exists, so it
    needs the full URL set — sitemap/llms discovery, not link crawl.
    """
    base, dash, code = url.rpartition("-")
    if not dash or code.lower() not in SUFFIX_CODES or code in keep:
        return False
    return base in url_set
EXCLUDE_RE = re.compile(
    r"(?i)(?:^|/)(?:changelog|change-log|license|licence|code[-_]of[-_]conduct|blog|news"
    r"|archive|archived|deprecated|legacy|old|superseded|tags?|search|print|node_modules"
    r"|__tests__|tests?|spec)(?:/|$|\.[a-z]+$)"
)

ASSET_EXT = frozenset(
    """png jpg jpeg gif svg webp avif ico bmp tiff pdf zip gz tar tgz rar 7z exe dmg pkg
    css js mjs map woff woff2 ttf otf eot mp4 webm mov avi mp3 wav ogg csv xlsx docx pptx
    json xml rss atom yaml yml txt""".split()
)
BAD_SCHEMES = ("mailto:", "javascript:", "tel:", "sms:", "data:", "blob:", "ftp:", "file:")


def bad_href(href: str) -> bool:
    """Raw hrefs that must never be enqueued."""
    href = (href or "").strip()
    return not href or href.startswith("#") or href.lower().startswith(BAD_SCHEMES)


def is_asset(url: str) -> bool:
    """Non-page targets, by extension (.md is a page: llms.txt sites serve markdown)."""
    last = urlparse(url).path.rsplit("/", 1)[-1]
    return "." in last and last.rsplit(".", 1)[-1].lower() in ASSET_EXT


def locale_dupe(url: str, keep: frozenset = frozenset()) -> bool:
    """True if the path sits under a locale directory the user did not ask for."""
    for seg in urlparse(url).path.strip("/").split("/"):
        if seg and seg not in keep and LOCALE_RE.match(seg):
            return True
    return False


def md_variant(url: str) -> str:
    """Markdown twin of a docs URL, Mintlify / llms.txt convention: `<path>.md`.

    This is exactly the URL an llms.txt link list already points at, so the
    round-trip (strip `.md` for the canonical page name, re-append here to fetch)
    is exact. Idempotent when handed an already-.md url. The site root has no
    twin — the caller's md-probe just re-fetches it as HTML.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path or path.endswith(".md"):
        return url
    return parsed._replace(path=f"{path}.md").geturl()


def _md_canonical(twin_url: str) -> str:
    """Canonical page url for a `.md` twin link.

    Strips `.md`, then a trailing `/index` or `/readme` — Cloudflare twins live
    at `<path>/index.md` and GitBook lists a folder's landing page as
    `<path>/readme.md` (docs.paperstrategy.xyz: `/readme` 307s to `/`) while the
    sitemap lists `<path>`. Without the strip the same page enters the crawl
    twice under two urls.
    """
    page = twin_url[:-3]
    for stem in ("/index", "/readme"):
        if page.lower().endswith(stem):
            return page[: -len(stem)]
    return page


def md_twins(llms_urls):
    """Canonical page urls for the `.md` twins in an llms.txt link list.

    Empty when the llms.txt lists no per-page `.md` (a generic bundle-only
    llms.txt — llms-full.txt and friends). An empty result is the signal to
    NOT treat the crawl as markdown-capable, so pages fetch their html directly
    instead of double-fetching a twin that 404s.
    """
    return [_md_canonical(u) for u in llms_urls if u.endswith(".md")]


def md_twin_map(llms_urls):
    """Canonical page url -> the EXACT twin url the llms.txt listed.

    Twin conventions differ per site (`<path>.md` on Mintlify/ReadMe,
    `<path>/index.md` on Cloudflare) — the llms.txt link is ground truth, so
    fetch that instead of re-deriving a convention with md_variant.
    """
    return {clean_url(_md_canonical(u)): u for u in llms_urls if u.endswith(".md")}


def reject_url(url, host, scope, max_depth, keep_locales=frozenset()):
    """Reason this URL is out of bounds, or None if it should be fetched."""
    try:
        parsed = urlparse(url)
    except ValueError:
        # Sitemaps and llms.txt are untrusted input: gitbook.com/docs/llms-full.txt
        # ships the placeholder https://[organization-name].gitbook.io/[site-title],
        # which urlparse rejects as an invalid IPv6 host and used to kill the run.
        return "unparseable"
    if norm_host(parsed.netloc) != norm_host(host):
        return "offsite"
    if is_asset(url):
        return "asset"
    depth = path_depth(url, scope)
    if depth < 0:
        return "out_of_scope"
    if depth > max_depth:
        return "too_deep"
    if locale_dupe(url, keep_locales):
        return "locale_dupe"
    if EXCLUDE_RE.search(parsed.path):
        return "excluded_pattern"
    return None
