"""Gravitee developer portal. The Angular SPA is never scraped — every doc page
comes from the portal's public JSON API (the same calls the SPA makes)."""

import json
import re
from urllib.parse import urlparse

from .base import Adapter, Doc
from .detection import has
from .swaggerhub import render as render_spec

# ponytail: DEFAULT is the only environment seen live; read assets/config.json if a portal differs.
PORTAL_API = "/portal/environments/DEFAULT"
DOC_TYPES = ("MARKDOWN", "SWAGGER", "ASCIIDOC", "ASYNCAPI")
API_ID = re.compile(r"/catalog/api/([0-9a-f-]{36})")


# Marker: the portal theme's CSS variables (--gv-theme-*), inlined in the static
# shell. Live: https://developer.quickintel.io/catalog/api/<id>/doc (2026-10-21).
def detect(fp):
    return has(fp, "--gv-theme")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "page"


def _json(ctx, url):
    """GET + parse, or record the failure and return None."""
    response = ctx.get(url)
    status = getattr(response, "status", 0)
    reason = f"http {status}"
    if status == 200:
        try:
            return json.loads(getattr(response, "body", b"") or b"{}")
        except ValueError:  # an upgraded portal or other environment answering HTML
            reason = "non-JSON 200 (portal API moved?)"
    ctx.ledger.enqueue(url)
    ctx.ledger.record(url, "failed_fetch", reason, tier="api", status=status)
    ctx.emit({"type": "warning", "url": url, "message": f"⚠️  {reason}: {url}"})
    return None


def _page_docs(page, url_for, source):
    """One page -> Docs. A spec page renders per tag, plus the raw spec (lossless)."""
    content = page.get("content") or ""
    title = page.get("name") or "Untitled"
    header = f"# {title}\n\nSource page: {source}\n\n"
    if page.get("type") == "SWAGGER":
        try:
            yield from render_spec(json.loads(content), url_for)
        except ValueError:
            pass  # YAML spec: the raw doc below still carries all of it
        yield Doc(url=url_for("spec"), markdown=f"{header}```\n{content.strip()}\n```\n")
        return
    yield Doc(url=url_for(""), markdown=header + content.strip() + "\n")


def harvest(ctx):
    """The API named in the start URL, or every public API when it names none."""
    base = ctx.origin + PORTAL_API
    match = API_ID.search(urlparse(ctx.scope).path)
    if match:
        api_ids = [match.group(1)]
    else:
        listing = _json(ctx, f"{base}/apis?size=-1") or {}
        api_ids = [api["id"] for api in listing.get("data", [])]

    stems = set()
    for api_id in api_ids:
        api = _json(ctx, f"{base}/apis/{api_id}") or {}
        api_slug = _slug(api.get("name") or api_id)
        pages = _json(ctx, f"{base}/apis/{api_id}/pages?size=-1&homepage=false") or {}
        for page in sorted(pages.get("data", []), key=lambda p: p.get("order", 0)):
            if page.get("type") not in DOC_TYPES:
                continue
            page = _json(ctx, f"{base}/apis/{api_id}/pages/{page['id']}?include=content")
            if page is None:
                continue
            stem = f"{ctx.origin}/{api_slug}/{_slug(page.get('name'))}"
            if stem in stems:  # two pages share a title (e.g. "Overview" in two folders)
                stem += f"-{page['id'][:8]}"
            stems.add(stem)
            source = f"{ctx.origin}/catalog/api/{api_id}/doc?page={page['id']}"
            url_for = lambda part, stem=stem: f"{stem}-{part}" if part else stem
            for doc in _page_docs(page, url_for, source):
                ctx.ledger.enqueue(doc.url)  # enqueue before the engine writes it
                yield doc


ADAPTER = Adapter(
    name="gravitee",
    detect=detect,
    fetch_mode="api",
    harvest=harvest,
)
