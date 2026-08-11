#!/usr/bin/env python
"""Refresh the committed platform fixtures.

    engine/.venv/bin/python engine/tests/fixtures/capture.py            # everything
    engine/.venv/bin/python engine/tests/fixtures/capture.py gitbook    # one platform

Static captures are a plain GET with a browser User-Agent — exactly what the
static tier sees at runtime, so `detect()` can be exercised offline. Rendered
captures are headless chromium DOM, i.e. what the dynamic tier sees. Both write
`meta.json` next to the files with the response headers (lowercased), the status,
and the trafilatura extraction size, which is what calibrates THIN_CHARS.

Politeness: >=1s between requests to the same host. Nothing here is a crawl.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from iofast_engine.extract import extract_markdown  # noqa: E402

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HERE = Path(__file__).resolve().parent
SIZE_CAP = 1_500_000
MIN_INTERVAL = 1.0
DATA_URI_RE = re.compile(r'(src|href)="data:[^"]{200,}"')

_last_call: dict[str, float] = {}

# (slug, url) per platform; `render` lists the slugs that also get a browser capture.
TARGETS = {
    "gitbook": {
        "pages": [
            ("landing", "https://docs.gitbook.com/"),
            ("blocks", "https://docs.gitbook.com/creating-content/blocks"),
        ],
        "render": ["landing", "blocks"],
        "notes": "GitBook's own docs. Static HTML is a Next.js payload; rendered is the real DOM.",
    },
    "mintlify": {
        "pages": [
            ("landing", "https://mintlify.com/docs"),
            ("quickstart", "https://mintlify.com/docs/quickstart"),
        ],
        "render": ["quickstart"],
        "notes": "Mintlify's own docs, Next.js app router.",
    },
    "docusaurus": {
        "pages": [
            ("landing", "https://docusaurus.io/docs"),
            ("installation", "https://docusaurus.io/docs/installation"),
        ],
        "render": [],
        "notes": "Static-site generator: server HTML is already complete, no rendered variant.",
    },
    "readme": {
        "pages": [
            ("landing", "https://docs.readme.com/"),
            ("getting-started", "https://docs.readme.com/main/docs/getting-started"),
        ],
        "render": ["landing", "getting-started"],
        "notes": "ReadMe's own docs. developers.notion.com is also ReadMe — do not use it for notion.",
    },
    "redocly": {
        "pages": [
            ("landing", "https://redocly.com/docs"),
            ("cli-installation", "https://redocly.com/docs/cli/installation"),
        ],
        "render": [],
        "notes": "Redocly Realm docs.",
    },
    "document360": {
        "pages": [
            ("landing", "https://docs.document360.com/docs"),
            ("getting-started", "https://docs.document360.com/docs/document360-getting-started"),
        ],
        "render": ["getting-started"],
        "notes": "Document360's own knowledge base.",
    },
    "readthedocs": {
        "pages": [
            ("landing", "https://docs.readthedocs.io/en/stable/"),
            ("tutorial", "https://docs.readthedocs.io/en/stable/tutorial/index.html"),
        ],
        "render": [],
        "notes": "Sphinx + furo theme, server-rendered.",
    },
    "notion": {
        "pages": [
            ("citybld", "https://worldbld.notion.site/CityBLD-Public-Documentation-a14296b5649c4cb8bfc3a0065675d90b"),
            ("theiagen", "https://theiagen.notion.site/Theiagen-Public-Health-Resources-a4bd134b0c5c4fe39870e21029a30566"),
        ],
        "render": ["citybld", "theiagen"],
        "notes": "Public *.notion.site pages. Static HTML is a ~19KB shell — the content is JS-only.",
    },
    "swaggerhub": {
        "pages": [
            ("registry-api-spa", "https://app.swaggerhub.com/apis-docs/swagger-hub/registry-api/1.3.0"),
        ],
        "api": [("spec.json", "https://api.swaggerhub.com/apis/swagger-hub/registry-api/1.3.0")],
        "render": [],
        "notes": "The apis-docs SPA page 302s to a silent-login proxy for a plain GET — that redirect body IS the static fixture, and it is why the OpenAPI spec JSON is the real content source.",
    },
    "confluence": {
        "pages": [("kafka-index", "https://cwiki.apache.org/confluence/display/KAFKA/Index")],
        "api": [
            (
                "api-content.json",
                "https://cwiki.apache.org/confluence/rest/api/content?limit=2&expand=body.storage",
            )
        ],
        "render": [],
        "notes": "Apache's public Confluence. The REST API returns storage-format body directly.",
    },
    "negatives": {
        "pages": [("notfound", "https://docs.readme.com/main/docs/creating-a-page")],
        "render": [],
        "notes": (
            "plain.html is handwritten (no platform markers). cloudflare-challenge.html is "
            "synthetic — a real challenge is not reproducibly capturable. notfound.html is a "
            "real 404 body."
        ),
    },
}

PLAIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Widget API — Docs</title>
</head>
<body>
  <nav><a href="/">Home</a> <a href="/docs">Docs</a></nav>
  <main>
    <h1>Getting started with the Widget API</h1>
    <p>The Widget API is a small HTTP service for creating and listing widgets. Every
    request needs an API key, passed in the <code>X-Api-Key</code> header. Keys are issued
    per project and can be rotated at any time without downtime.</p>
    <h2>Creating a widget</h2>
    <p>Send a POST request to <code>/v1/widgets</code> with a JSON body containing a name
    and an optional colour. The response carries the new widget id, which you will need
    for every later call about that widget.</p>
    <pre><code>curl -X POST https://api.example.com/v1/widgets \\
  -H "X-Api-Key: $KEY" -d '{"name": "left-handed sprocket"}'</code></pre>
    <h2>Listing widgets</h2>
    <p>A GET request to <code>/v1/widgets</code> returns your widgets, newest first, twenty
    per page. Pass <code>?cursor=</code> with the cursor from the previous response to walk
    the rest of the list. An empty list means you have reached the end.</p>
    <h2>Errors</h2>
    <p>Errors use standard status codes. A 401 means the key is missing or wrong, a 404
    means the widget id does not exist, and a 429 means you are sending requests faster
    than the plan allows. The body always contains a machine-readable code.</p>
  </main>
  <footer><p>&copy; Example Inc.</p></footer>
</body>
</html>
"""

CHALLENGE_HTML = """<!doctype html>
<html lang="en-US">
<head>
  <meta charset="utf-8">
  <title>Just a moment...</title>
  <meta http-equiv="refresh" content="390">
</head>
<body class="no-js">
  <div class="main-wrapper">
    <h1><span>docs.example.com</span></h1>
    <h2 id="challenge-running">Checking if the site connection is secure</h2>
    <div id="challenge-error-text">Enable JavaScript and cookies to continue</div>
  </div>
  <script>window._cf_chl_opt={cvId:'3',cType:'managed',cRay:'8f0000000000',cH:'x'};</script>
</body>
</html>
"""

SYNTHETIC = {
    "plain.html": (PLAIN_HTML, 200, {"content-type": "text/html; charset=utf-8"}),
    "cloudflare-challenge.html": (
        CHALLENGE_HTML,
        403,
        {"content-type": "text/html; charset=UTF-8", "cf-mitigated": "challenge", "server": "cloudflare"},
    ),
}


def polite(url):
    host = urllib.parse.urlparse(url).netloc
    last = _last_call.get(host)
    if last is not None:
        wait = MIN_INTERVAL - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def fetch(url):
    """(status, headers-lowercased, text). An error status is a valid fixture, not a failure."""
    polite(url)
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, headers, body = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, headers, body = error.code, error.headers, error.read()
    return (
        status,
        {k.lower(): v for k, v in headers.items()},
        body.decode("utf-8", errors="ignore"),
    )


def render(urls):
    """{url: html} from headless chromium. One browser for the whole batch."""
    from playwright.sync_api import sync_playwright

    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        for url in urls:
            polite(url)
            try:
                page.goto(url, wait_until="networkidle", timeout=20_000)
            except Exception:
                page.wait_for_timeout(5_000)
            out[url] = page.content()
        browser.close()
    return out


def cap(text):
    """(text, note). Over the size cap, inline base64 data: URIs go first."""
    if len(text) <= SIZE_CAP:
        return text, None
    stripped = DATA_URI_RE.sub(r'\1="data:stripped"', text)
    note = f"stripped inline data: URIs ({len(text)} -> {len(stripped)} bytes)"
    return stripped, note


def entry(file, url, status, headers, text, variant, synthetic=False, note=None):
    row = {
        "file": file,
        "url": url,
        "captured": date.today().isoformat(),
        "status": status,
        "headers": headers,
        "variant": variant,
        "extracted_chars": len(extract_markdown(text, url)),
        "synthetic": synthetic,
    }
    if note:
        row["note"] = note
    return row


def capture(platform, spec):
    out_dir = HERE / ("negatives" if platform == "negatives" else f"platforms/{platform}")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    for slug, url in spec.get("pages", []):
        status, headers, text = fetch(url)
        text, note = cap(text)
        file = f"{slug}.html" if platform == "negatives" else f"static-{slug}.html"
        (out_dir / file).write_text(text, encoding="utf-8")
        rows.append(entry(file, url, status, headers, text, "static", note=note))
        print(f"  {platform}/{file}: {status} {len(text)}B -> {rows[-1]['extracted_chars']} chars")

    for file, url in spec.get("api", []):
        status, headers, text = fetch(url)
        text, _ = cap(text)
        (out_dir / file).write_text(text, encoding="utf-8")
        rows.append(entry(file, url, status, headers, text, "api"))
        print(f"  {platform}/{file}: {status} {len(text)}B")

    wanted = {slug: url for slug, url in spec.get("pages", []) if slug in spec.get("render", [])}
    if wanted:
        rendered = render(list(wanted.values()))
        for slug, url in wanted.items():
            text, note = cap(rendered[url])
            file = f"rendered-{slug}.html"
            (out_dir / file).write_text(text, encoding="utf-8")
            # A rendered DOM has no response of its own; it carries the static one's status.
            static = next(r for r in rows if r["file"] == f"static-{slug}.html")
            rows.append(
                entry(file, url, static["status"], static["headers"], text, "rendered", note=note)
            )
            print(f"  {platform}/{file}: {len(text)}B -> {rows[-1]['extracted_chars']} chars")

    if platform == "negatives":
        for file, (text, status, headers) in SYNTHETIC.items():
            (out_dir / file).write_text(text, encoding="utf-8")
            rows.append(entry(file, f"synthetic://{file}", status, headers, text, "static", True))
            print(f"  {platform}/{file}: synthetic -> {rows[-1]['extracted_chars']} chars")

    meta = {"platform": platform, "pages": rows, "notes": spec["notes"]}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def main(argv):
    wanted = argv or list(TARGETS)
    for platform in wanted:
        if platform not in TARGETS:
            raise SystemExit(f"unknown platform: {platform} (have: {', '.join(TARGETS)})")
        print(platform)
        capture(platform, TARGETS[platform])


if __name__ == "__main__":
    main(sys.argv[1:])
