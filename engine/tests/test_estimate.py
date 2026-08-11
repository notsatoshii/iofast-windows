"""The 1-second panel budget is a HARD requirement — proven against a slow
server fixture, not a fast real site. The must-fail case (server slower than
the budget) comes first: the guard is only trusted once it demonstrably fires."""

import http.server
import threading
import time

import pytest

from iofast_engine.estimate import run_estimate

SITEMAP = (
    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>http://HOST/docs/a</loc></url>"
    "<url><loc>http://HOST/docs/b</loc></url>"
    "<url><loc>http://HOST/blog/x</loc></url>"  # out of scope
    "</urlset>"
)

INDEX = (
    '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>http://HOST/sitemap-child.xml</loc></sitemap>"
    "</sitemapindex>"
)

LLMS = "# Docs\n- [A](/docs/a)\n- [B](/docs/b)\n- [C](/docs/c)\n- [Ext](https://other.example/x)\n"


def serve(routes, delay=0.0):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if delay:
                time.sleep(delay)
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            host = f"127.0.0.1:{self.server.server_address[1]}"
            data = body.replace("HOST", host).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def slow_site():
    server, base = serve({"/sitemap.xml": SITEMAP, "/llms.txt": LLMS}, delay=3.0)
    yield base
    server.shutdown()


def test_slow_server_cannot_blow_the_budget(slow_site):
    """MUST-FAIL fixture: the server answers in 3s, the budget is 0.8s.
    The estimate returns empty INSIDE the budget instead of waiting."""
    started = time.monotonic()
    result = run_estimate(f"{slow_site}/docs/")
    wall = time.monotonic() - started
    assert wall < 1.2, f"budget blown: {wall:.2f}s wall for a 0.8s budget"
    assert result["page_count"] is None
    assert result["source"] is None
    assert result["scope"] == "/docs/"


def test_sitemap_estimate_counts_in_scope_only():
    server, base = serve({"/sitemap.xml": SITEMAP, "/llms.txt": LLMS})
    try:
        result = run_estimate(f"{base}/docs/")
        assert result["source"] == "sitemap"  # sitemap wins over llms
        assert result["page_count"] == 2  # /blog/x excluded by scope
    finally:
        server.shutdown()


def test_llms_fallback_when_no_sitemap():
    server, base = serve({"/llms.txt": LLMS})
    try:
        result = run_estimate(f"{base}/docs/")
        assert result["source"] == "llms"
        assert result["page_count"] == 3  # other.example excluded (origin filter)
    finally:
        server.shutdown()


def test_sitemap_index_followed_one_level():
    server, base = serve({"/sitemap.xml": INDEX, "/sitemap-child.xml": SITEMAP})
    try:
        result = run_estimate(f"{base}/docs/")
        assert result["source"] == "sitemap"
        assert result["page_count"] == 2
    finally:
        server.shutdown()


def test_no_server_at_all_returns_fast():
    started = time.monotonic()
    result = run_estimate("http://127.0.0.1:9/docs/")  # port 9: discard, refuses
    assert time.monotonic() - started < 1.2
    assert result["source"] is None
