"""Confluence. Harvested through the REST API — the rendered wiki page is never scraped."""

import json
import os
from urllib.parse import urlparse, urlunparse

from markdownify import markdownify as md

from ..extract import page_content, strip_noise
from .base import Adapter, Doc
from .detection import has

# Path segments that start the page part of a Confluence URL; everything before one
# of them is the context path (/confluence on cwiki.apache.org, /wiki on Atlassian Cloud).
PAGE_SEGMENTS = ("display", "spaces", "pages")
MAX_API_CALLS = 20  # ponytail: hard stop on pagination; the writer's max_pages caps output


# Markers: the Confluence context/base-url meta tags and the AJS page metadata:
#   <meta id="confluence-base-url" name="confluence-base-url" content="…">
#   <meta id="confluence-context-path" name="confluence-context-path" content="/confluence">
#   <meta name="ajs-page-id" content="27821303">
# tests/fixtures/platforms/confluence/static-kafka-index.html.
def detect(fp):
    return has(fp, 'name="confluence-base-url"', 'name="confluence-context-path"')


def _strip_query(url):
    """Ledger URLs never carry a query string."""
    parts = urlparse(url)
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))


def _base_and_space(url):
    """(rest base url, space key or None) from a page URL."""
    parts = urlparse(url)
    segments = [s for s in parts.path.split("/") if s]
    origin = f"{parts.scheme}://{parts.netloc}"
    for index, segment in enumerate(segments):
        if segment in PAGE_SEGMENTS:
            context = "/".join(segments[:index])
            space = segments[index + 1] if len(segments) > index + 1 else None
            return f"{origin}/{context}".rstrip("/"), space
    return f"{origin}/{'/'.join(segments)}".rstrip("/"), None


def _headers():
    """Token from the environment only — never from argv, where ps(1) would leak it."""
    token = os.environ.get("CONFLUENCE_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else None


def _markdown(title, storage, url):
    text, _ = page_content(storage, url)
    if not text:  # storage format is fragment HTML; trafilatura often finds no article
        text = md(strip_noise(storage), heading_style="ATX").strip()
    return f"# {title}\n\n{text}\n"


def harvest(ctx):
    """Paginate /rest/api/content?expand=body.storage, one Doc per page."""
    base, space = _base_and_space(ctx.scope)
    query = "?expand=body.storage&limit=25" + (f"&spaceKey={space}" if space else "")
    url = f"{base}/rest/api/content{query}"
    headers = _headers()

    for _ in range(MAX_API_CALLS):
        response = ctx.get(url, headers=headers)  # politeness lives in discovery._get
        status = getattr(response, "status", 0)
        if status != 200:
            clean = _strip_query(url)
            ctx.ledger.enqueue(clean)
            ctx.ledger.record(clean, "failed_fetch", f"http {status}", tier="api", status=status)
            ctx.emit({"type": "warning", "url": clean, "message": f"⚠️  http {status}: {clean}"})
            return
        payload = json.loads(getattr(response, "body", b"") or b"{}")
        for result in payload.get("results") or []:
            storage = ((result.get("body") or {}).get("storage") or {}).get("value") or ""
            webui = (result.get("_links") or {}).get("webui") or f"/pages/{result.get('id')}"
            doc_url = _strip_query(base + webui)
            ctx.ledger.enqueue(doc_url)  # enqueue before the engine writes it
            yield Doc(url=doc_url, markdown=_markdown(result.get("title", ""), storage, doc_url))
        next_path = (payload.get("_links") or {}).get("next")
        if not next_path:
            return
        url = (payload["_links"].get("base") or base) + next_path


ADAPTER = Adapter(
    name="confluence",
    detect=detect,
    fetch_mode="api",
    harvest=harvest,
)
