"""HTML -> markdown. Pure: takes html text in, never touches a fetcher Response."""

import re

from lxml import html as lxml_html
from markdownify import markdownify as md

CONTENT_SELECTORS = ("main", "article", 'div[role="main"]', '[role="main"]')
DROP_TAGS = ("nav", "aside", "footer", "header", "script", "style", "noscript", "svg", "form")
NOISE_RE = re.compile(
    r"(?i)(breadcrumb|cookie|consent|edit[-_]?(this[-_]?)?page|feedback|pagination|prev-next"
    r"|sidebar|toc|table-of-contents|skip-link|announcement|banner|social-share|newsletter)"
)


def strip_noise(html: str) -> str:
    """Remove nav/breadcrumb/'Edit this page'/cookie chrome before conversion."""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return html
    for element in tree.xpath("|".join(f"//{tag}" for tag in DROP_TAGS)):
        element.getparent().remove(element) if element.getparent() is not None else None
    for element in tree.xpath("//*[@class or @id]"):
        marker = f"{element.get('class', '')} {element.get('id', '')}"
        if NOISE_RE.search(marker) and element.getparent() is not None:
            element.getparent().remove(element)
    return lxml_html.tostring(tree, encoding="unicode", method="html")


def extract_markdown(html: str, url: str) -> str:
    """trafilatura markdown, favouring precision; empty string when it finds no article."""
    import trafilatura

    try:
        text = trafilatura.extract(
            html,
            url=url,
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


def page_content(html: str, url: str, selectors=CONTENT_SELECTORS):
    """(markdown, how) using: platform selectors -> trafilatura -> nothing (never raw body)."""
    selector_html = select_first(html, selectors)

    if selector_html:
        cleaned = strip_noise(selector_html)
        text = extract_markdown(cleaned, url)
        if text:
            return text, "selector+trafilatura"
        text = md(cleaned, heading_style="ATX").strip()
        if text:
            return text, "selector+markdownify"

    text = extract_markdown(strip_noise(html), url)
    return (text, "trafilatura") if text else ("", "none")
