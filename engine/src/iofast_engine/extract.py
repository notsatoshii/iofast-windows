"""HTML -> markdown. Pure: takes html text in, never touches a fetcher Response."""

import re
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html
from markdownify import markdownify as md

CONTENT_SELECTORS = ("main", "article", 'div[role="main"]', '[role="main"]')
DROP_TAGS = ("nav", "aside", "footer", "header", "script", "style", "noscript", "svg", "form")
# Noise classes/ids that are genuinely site chrome. "toc" is excluded here because
# MkDocs Material and other doc frameworks put link-heavy TOC lists inside the
# main article body — stripping them loses real navigation content. The selectors
# below target only elements that are structurally outside the article flow.
NOISE_RE = re.compile(
    r"(?i)(breadcrumb|cookie|consent|edit[-_]?(this[-_]?)?page|feedback|pagination|prev-next"
    r"|sidebar|table-of-contents|skip-link|announcement|banner|social-share|newsletter)"
)


def _resolve_base(html: str, url: str) -> str:
    """Extract <base href> from the HTML if present, else derive from the URL.

    Directory-style URLs must keep their trailing slash so that relative
    ``../sibling/`` links resolve against the directory, not its parent.
    ``urljoin("https://x.com/a/b/", "../c/")`` → ``https://x.com/a/c/`` ✓
    ``urljoin("https://x.com/a/b",  "../c/")`` → ``https://x.com/c/``   ✗
    """
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        pass
    else:
        bases = tree.xpath("//base[@href]")
        if bases:
            return bases[0].get("href", "").strip() or url

    # Preserve trailing slash on directory URLs so ../ resolves correctly.
    parsed = urlparse(url)
    if parsed.path and not parsed.path.endswith("/") and "." not in parsed.path.rsplit("/", 1)[-1]:
        return url + "/"
    return url


def _make_links_absolute(html: str, base: str) -> str:
    """Resolve every relative href/src against base before conversion."""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return html
    tree.make_links_absolute(base, handle_failures="discard")
    return lxml_html.tostring(tree, encoding="unicode", method="html")


def strip_noise(html: str) -> str:
    """Remove nav/breadcrumb/'Edit this page'/cookie chrome before conversion.

    Only strips elements whose class/id matches NOISE_RE AND that live outside
    the main content container (main/article/[role=main]). This preserves
    link-heavy lists that are part of the article body while still removing
    genuine site chrome like sidebars and footers.
    """
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return html
    for element in tree.xpath("|".join(f"//{tag}" for tag in DROP_TAGS)):
        element.getparent().remove(element) if element.getparent() is not None else None

    # Identify content containers — noise inside these is kept (it's article content).
    content_roots = set()
    for sel in CONTENT_SELECTORS:
        try:
            for el in tree.cssselect(sel):
                content_roots.add(id(el))
        except Exception:
            continue

    for element in tree.xpath("//*[@class or @id]"):
        marker = f"{element.get('class', '')} {element.get('id', '')}"
        if not NOISE_RE.search(marker):
            continue
        # Skip removal if this element lives inside a content container.
        parent = element.getparent()
        inside_content = False
        cur = element
        while cur is not None:
            if id(cur) in content_roots:
                inside_content = True
                break
            cur = cur.getparent()
        if inside_content:
            continue
        if parent is not None:
            parent.remove(element)
    return lxml_html.tostring(tree, encoding="unicode", method="html")


def extract_markdown(html: str, url: str, *, _already_resolved: bool = False) -> str:
    """trafilatura markdown, favouring precision; empty string when it finds no article.

    When ``_already_resolved`` is True, links have already been made absolute
    by the caller (page_content) and we skip re-resolution. This avoids
    double-resolving and ensures <base href> is respected even when the
    selector extraction strips the <head>.
    """
    import trafilatura

    if _already_resolved:
        resolved_html, base = html, url
    else:
        base = _resolve_base(html, url)
        resolved_html = _make_links_absolute(html, base)
    try:
        text = trafilatura.extract(
            resolved_html,
            url=base,
            output_format="markdown",
            favor_precision=True,
            include_links=True,
            include_formatting=True,
            include_tables=True,
        )
    except Exception:
        return ""
    return (text or "").strip()


def select_first(html: str, selectors) -> str:
    """Outer HTML of the first element matching any selector (lxml + cssselect)."""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return ""
    for selector in selectors:
        try:
            found = tree.cssselect(selector)
        except Exception:
            continue
        if found:
            return lxml_html.tostring(found[0], encoding="unicode", method="html")
    return ""


def _normalize_bullets(text: str) -> str:
    """Normalize markdownify's '*' bullets to '-' for consistency with trafilatura."""
    return re.sub(r"(?m)^(\s*)\* ", r"\1- ", text)


def _resolve_links_in_html(html: str, url: str) -> tuple[str, str]:
    """Resolve relative links in html against the correct base. Returns (resolved_html, base).

    Must run BEFORE strip_noise so that <base href> is still present in the tree.
    """
    base = _resolve_base(html, url)
    return _make_links_absolute(html, base), base


def page_content(html: str, url: str, selectors=CONTENT_SELECTORS):
    """(markdown, how) using: platform selectors -> trafilatura -> markdownify fallback.

    Links are resolved against the correct base (respecting <base href> and
    preserving trailing slashes) BEFORE noise stripping, so the base tag is
    still in the tree when we read it. When trafilatura's precision mode drops
    link-heavy article content (common on MkDocs/Docusaurus landing sections),
    we fall back to markdownify which preserves all list items.
    """
    # Resolve links once up front — before any strip_noise call removes <base>.
    resolved_html, base = _resolve_links_in_html(html, url)

    selector_html = select_first(resolved_html, selectors)

    if selector_html:
        cleaned = strip_noise(selector_html)
        # Links already absolute from _resolve_links_in_html; tell extract_markdown
        # not to re-resolve (which would break when select_first stripped <base>).
        text = extract_markdown(cleaned, base, _already_resolved=True)
        if text:
            # Trafilatura precision can drop link-only lists that are real content.
            # Compare <li> counts: if the output has fewer list items than the HTML,
            # fall back to markdownify which preserves all of them.
            html_li_count = cleaned.lower().count("<li")
            out_li_count = text.count("\n- ") + text.count("\n* ") + (1 if text.startswith("- ") or text.startswith("* ") else 0)
            if html_li_count > 0 and out_li_count < html_li_count:
                md_text = _normalize_bullets(md(cleaned, heading_style="ATX")).strip()
                if md_text and len(md_text) > len(text):
                    return md_text, "selector+markdownify"
            return text, "selector+trafilatura"
        text = _normalize_bullets(md(cleaned, heading_style="ATX")).strip()
        if text:
            return text, "selector+markdownify"

    cleaned_full = strip_noise(resolved_html)
    text = extract_markdown(cleaned_full, base, _already_resolved=True)
    if text:
        html_li_count = cleaned_full.lower().count("<li")
        out_li_count = text.count("\n- ") + text.count("\n* ") + (1 if text.startswith("- ") or text.startswith("* ") else 0)
        if html_li_count > 0 and out_li_count < html_li_count:
            md_text = _normalize_bullets(md(cleaned_full, heading_style="ATX")).strip()
            if md_text and len(md_text) > len(text):
                return md_text, "markdownify"
        return text, "trafilatura"
    # Fallback to markdownify only when trafilatura found nothing BUT the HTML
    # actually has content-bearing elements. An empty/boilerplate-only page must
    # stay empty — inventing nav chrome as "content" is worse than skipping it.
    has_content_elements = bool(re.search(r"<(p|h[1-6]|li|td|pre|blockquote)[\s>]", cleaned_full, re.I))
    if has_content_elements:
        text = _normalize_bullets(md(cleaned_full, heading_style="ATX")).strip()
    return (text, "markdownify") if text else ("", "none")
