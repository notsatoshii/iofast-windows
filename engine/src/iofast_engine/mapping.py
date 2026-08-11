"""Map pass: section tree from discovered URLs, before any scrape.

Module level stays pure (offline-testable); map_run() lazy-imports the
network side, same pattern as discovery._get.
"""

from urllib.parse import urljoin, urlparse

from .urls import bad_href, clean_url

# Generic nav containers, tried in order when the adapter has no nav_selector.
NAV_SELECTORS = ("nav", '[role="navigation"]', "aside")


def section_tree(urls, scope: str) -> dict:
    """Nest in-scope URLs by path segment under the scope prefix.

    Node: {"pages": direct page count, "total": subtree count, "children": {seg: node}}.
    """
    root = {"pages": 0, "total": 0, "children": {}}
    for url in urls:
        path = urlparse(url).path
        if path.rstrip("/") == scope.rstrip("/"):
            path = scope  # the scope-root page counts as a direct page of the root
        if not path.startswith(scope):
            continue
        rest = [seg for seg in path[len(scope):].strip("/").split("/") if seg]
        root["total"] += 1
        node = root
        for seg in rest[:-1]:  # every segment but the last is a section
            node = node["children"].setdefault(seg, {"pages": 0, "total": 0, "children": {}})
            node["total"] += 1
        node["pages"] += 1  # the URL itself is a direct page of its section
    return root


def render_tree(tree: dict, scope: str) -> str:
    """Human-readable indented tree with per-section page counts."""
    lines = [f"{scope}  ({tree['total']} pages)"]

    def walk(node, indent):
        for seg, child in sorted(node["children"].items()):
            lines.append(f"{indent}{seg}/  ({child['total']})")
            walk(child, indent + "  ")

    walk(tree, "  ")
    return "\n".join(lines)


def nav_links(html: str, base_url: str, nav_selector: str | None = None) -> list[str]:
    """Links out of the page's nav container(s). Pure lxml+cssselect."""
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return []
    selectors = (nav_selector,) + NAV_SELECTORS if nav_selector else NAV_SELECTORS
    links, seen = [], set()
    for selector in selectors:
        try:
            containers = tree.cssselect(selector)
        except Exception:
            continue
        for container in containers:
            for anchor in container.cssselect("a[href]"):
                href = anchor.get("href", "")
                if bad_href(href):
                    continue
                url = clean_url(urljoin(base_url, href))
                if url and url not in seen:
                    seen.add(url)
                    links.append(url)
        if links:  # first selector that yields links wins
            break
    return links


def map_run(resolved: str, scope: str | None, adapter=None, emit=None) -> dict:
    """Discovery-only pass: sitemap -> llms.txt -> nav parse. No scraping.

    Returns {"url", "platform", "scope", "discovery", "pages", "tree"}.
    """
    emit = emit or (lambda event: None)
    from .discovery import _body_text, _get, discover_llms, discover_sitemap, robots_sitemaps
    from .urls import scope_prefix

    parsed = urlparse(resolved)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc
    scope = scope or scope_prefix(resolved)

    def in_scope(urls):
        from .urls import norm_host, reject_url

        kept, seen = [], set()
        for url in urls:
            url = clean_url(url)
            if not url or url in seen:
                continue
            seen.add(url)
            # depth is uncapped in the map pass: the tree shows everything in scope
            if reject_url(url, host, scope, 10_000) is None:
                kept.append(url)
        return kept

    pages, discovery = [], "none"
    if adapter is not None and adapter.map_site is not None:
        mapped = adapter.map_site(None)
        if mapped:
            pages, discovery = in_scope(mapped), f"adapter ({adapter.name})"
    if not pages:
        found = discover_sitemap(origin, robots_sitemaps(origin), emit=emit)
        pages = in_scope([url for url, _ in found])
        discovery = "sitemap" if pages else discovery
    if not pages:
        _, found = discover_llms(origin, f"{origin}{scope.rstrip('/')}")
        pages = in_scope(found)
        discovery = "llms.txt" if pages else discovery
    if not pages:
        response = _get(resolved)
        if response is not None and getattr(response, "status", 0) == 200:
            selector = adapter.nav_selector if adapter else None
            pages = in_scope(nav_links(_body_text(response), resolved, selector))
            discovery = "nav" if pages else "none"

    return {
        "url": resolved,
        "platform": adapter.name if adapter else None,
        "scope": scope,
        "discovery": discovery,
        "pages": pages,
        "tree": section_tree(pages, scope),
    }
