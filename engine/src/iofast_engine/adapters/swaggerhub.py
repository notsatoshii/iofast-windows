"""SwaggerHub. The SPA is never scraped — the markdown is rendered from the API spec."""

import json
from urllib.parse import urlparse

from .base import Adapter, Doc
from .detection import has, host_is

API_ROOT = "https://api.swaggerhub.com/apis"


# Markers: the host, plus the login redirect body the SPA serves to an anonymous
# fetch ("Found. Redirecting to /apiproxy/sbid/login?silentLogin=true&isApiHub=false").
# tests/fixtures/platforms/swaggerhub/static-registry-api-spa.html (status 302, URL
# https://app.swaggerhub.com/apis-docs/swagger-hub/registry-api/1.3.0).
def detect(fp):
    return host_is(fp, "app.swaggerhub.com") or has(fp, "Redirecting to /apiproxy")


def _target(url):
    """/apis/{owner}/{api}[/{version}] or /apis-docs/{owner}/{api}[/{version}]."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and parts[0] in ("apis", "apis-docs"):
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError(f"not a SwaggerHub api url: {url}")
    return parts[0], parts[1], (parts[2] if len(parts) > 2 else None)


def _schema_name(schema):
    """A name, never a schema dump."""
    if not isinstance(schema, dict):
        return ""
    ref = schema.get("$ref") or (schema.get("items") or {}).get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        return f"[{name}]" if schema.get("type") == "array" else name
    return schema.get("type", "")


def _params(operation, spec):
    rows = []
    for param in operation.get("parameters") or []:
        ref = param.get("$ref")
        if ref:
            param = (spec.get("parameters") or {}).get(ref.rsplit("/", 1)[-1], {"name": ref})
        rows.append(
            f"| {param.get('name', '')} | {param.get('in', '')} | "
            f"{'yes' if param.get('required') else 'no'} | "
            f"{param.get('type') or _schema_name(param.get('schema'))} |"
        )
    if not rows:
        return []
    return ["**Parameters**", "", "| Name | In | Required | Type |", "|---|---|---|---|", *rows, ""]


def _operation(method, path, operation, spec):
    lines = [f"## {method.upper()} {path}", ""]
    for key in ("summary", "description"):
        if operation.get(key):
            lines += [operation[key].strip(), ""]
    lines += _params(operation, spec)
    responses = operation.get("responses") or {}
    if responses:
        lines += ["**Responses**", "", "| Code | Description | Schema |", "|---|---|---|"]
        for code, response in responses.items():
            description = (response.get("description") or "").replace("\n", " ").strip()
            lines.append(f"| {code} | {description} | {_schema_name(response.get('schema'))} |")
        lines.append("")
    return lines


def render(spec, doc_url_for):
    """One markdown doc per tag, plus an overview. Yields Doc(url, markdown)."""
    info = spec.get("info") or {}
    title = info.get("title", "API")
    version = info.get("version", "")
    overview = [f"# {title} {version}".strip(), ""]
    if info.get("description"):
        overview += [info["description"].strip(), ""]
    yield Doc(url=doc_url_for("overview"), markdown="\n".join(overview).strip() + "\n")

    by_tag = {}
    for path, operations in (spec.get("paths") or {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            for tag in operation.get("tags") or ["default"]:
                by_tag.setdefault(tag, []).append((method, path, operation))

    descriptions = {t.get("name"): t.get("description", "") for t in spec.get("tags") or []}
    for tag, operations in by_tag.items():
        slug = tag.lower().replace(" ", "-")
        lines = [f"# {title} — {tag}", ""]
        if descriptions.get(tag):
            lines += [descriptions[tag].strip(), ""]
        for method, path, operation in operations:
            lines += _operation(method, path, operation, spec)
        yield Doc(url=doc_url_for(slug), markdown="\n".join(lines).strip() + "\n")


def harvest(ctx):
    """One GET of the OpenAPI spec; every doc is rendered from it. Never touches the SPA."""
    owner, api, version = _target(ctx.scope)
    spec_url = f"{API_ROOT}/{owner}/{api}" + (f"/{version}" if version else "")
    response = ctx.get(spec_url)  # politeness (>=0.5s/host) lives in discovery._get
    status = getattr(response, "status", 0)
    if status != 200:
        ctx.ledger.enqueue(spec_url)
        ctx.ledger.record(spec_url, "failed_fetch", f"http {status}", tier="api", status=status)
        ctx.emit({"type": "warning", "url": spec_url, "message": f"⚠️  http {status}: {spec_url}"})
        return
    spec = json.loads(getattr(response, "body", b"") or b"{}")
    for doc in render(spec, lambda slug: f"{spec_url}/{slug}"):
        ctx.ledger.enqueue(doc.url)  # enqueue before the engine writes it
        yield doc


ADAPTER = Adapter(
    name="swaggerhub",
    detect=detect,
    fetch_mode="api",
    harvest=harvest,
)
