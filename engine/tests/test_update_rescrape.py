"""Conditional re-scrape (update=True): 304s keep files, changes re-fetch.

Real DocSpider against a localhost fixture server with genuine ETag /
If-None-Match semantics — the closest thing to the live contract that still
runs without external network. The must-fail case leads: a server that
IGNORES conditionals must still produce a correct full re-scrape (update mode
degrades to a normal crawl, never corrupts).
"""

import http.server
import json
import os
import threading

import pytest

from iofast_engine.runner import run_scrape

PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<main><h1>{title}</h1>
<p>Substantial fixture prose so the static tier clears the thin-page band
without escalating anywhere. This paragraph, and the next one, exist purely
to carry enough characters through extraction that the page records as ok.</p>
<p>{body}</p></main></body></html>"""


class Site:
    """Two docs pages with per-path ETags; honors If-None-Match unless told not to."""

    def __init__(self, honor_conditionals=True):
        self.honor = honor_conditionals
        self.hits = []  # (path, sent_conditional)
        self.bodies = {
            "/docs/": PAGE.format(title="Home", body="stable home content " * 8),
            "/docs/guide": PAGE.format(title="Guide", body="guide v1 content " * 8),
        }
        self.etags = {"/docs/": '"home-v1"', "/docs/guide": '"guide-v1"'}

        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = site._norm(self.path)
                if self.path == "/sitemap.xml":
                    host = f"127.0.0.1:{self.server.server_address[1]}"
                    urls = "".join(
                        f"<url><loc>http://{host}{p}</loc></url>" for p in site.bodies
                    )
                    body = (
                        '<?xml version="1.0"?>'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        f"{urls}</urlset>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path not in site.bodies:
                    self.send_response(404)
                    self.end_headers()
                    return
                conditional = self.headers.get("If-None-Match")
                site.hits.append((path, conditional is not None))
                if site.honor and conditional == site.etags[path]:
                    self.send_response(304)
                    self.send_header("ETag", site.etags[path])
                    self.end_headers()
                    return
                body = site.bodies[path].encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("ETag", site.etags[path])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _norm(self, path):
        # The engine strips trailing slashes; serve both spellings of a dir page.
        return path if path in self.bodies else path + "/"

    def stop(self):
        self.server.shutdown()


def scrape(site, out, update=False):
    return run_scrape(
        f"{site.base}/docs/", out=str(out), max_pages=10, update=update
    )


@pytest.mark.integration
def test_update_against_server_that_ignores_conditionals(tmp_path):
    """MUST-DEGRADE-SAFELY: no 304s ever -> update run is just a full re-scrape."""
    site = Site(honor_conditionals=False)
    try:
        first = scrape(site, tmp_path)
        assert first["exit_code"] == 0, first["manifest"]["counts"]
        second = scrape(site, tmp_path, update=True)
        assert second["exit_code"] == 0
        assert second["written"] == first["written"]  # everything re-fetched
        reasons = {r["reason"] for r in second["manifest"]["pages"]}
        assert "not modified (304)" not in reasons
    finally:
        site.stop()


@pytest.mark.integration
def test_update_304_keeps_files_and_validators_round_trip(tmp_path):
    site = Site()
    try:
        first = scrape(site, tmp_path)
        assert first["exit_code"] == 0, first["manifest"]["counts"]
        rows = {r["url"]: r for r in first["manifest"]["pages"]}
        assert all(r["etag"] for r in rows.values()), "validators must be captured"
        files_before = {
            name: os.path.getmtime(tmp_path / name)
            for name in os.listdir(tmp_path)
            if name.endswith(".md") and name != "report.md"  # report regenerates every run
        }

        site.hits.clear()
        second = scrape(site, tmp_path, update=True)
        assert second["exit_code"] == 0
        pages = {r["url"]: r for r in second["manifest"]["pages"]}
        assert all(r["reason"] == "not modified (304)" for r in pages.values()), pages
        assert second["written"] == 0  # nothing rewritten
        # Preflight/platform-detection GETs are legitimately unconditional;
        # the CRAWL fetches must all have been conditional (proven by the 304s
        # above) and there must be one per page.
        conditional = [path for path, sent in site.hits if sent]
        assert len(conditional) >= len(pages), site.hits
        for name, mtime in files_before.items():
            assert os.path.getmtime(tmp_path / name) == mtime, f"{name} was rewritten"
        # A changed page re-fetches and refreshes its validator.
        site.bodies["/docs/guide"] = PAGE.format(title="Guide", body="guide v2 content " * 8)
        site.etags["/docs/guide"] = '"guide-v2"'
        third = scrape(site, tmp_path, update=True)
        assert third["exit_code"] == 0
        with open(tmp_path / "manifest.json", encoding="utf-8") as fh:
            rows3 = {r["url"]: r for r in json.load(fh)["pages"]}
        guide = next(r for u, r in rows3.items() if u.endswith("/docs/guide"))
        assert guide["etag"] == '"guide-v2"'
        assert guide["reason"] != "not modified (304)"
    finally:
        site.stop()


def test_stale_checkpoint_cleared_on_fresh_run(tmp_path):
    """A leftover checkpoint.pkl must NOT auto-resume a fresh scrape (it would
    skip discovery with an empty ledger). The spider clears it on construction."""
    crawl = tmp_path / ".crawl"
    crawl.mkdir()
    stale = crawl / "checkpoint.pkl"
    stale.write_bytes(b"stale")
    site = Site()
    try:
        result = scrape(site, tmp_path)
        assert result["exit_code"] == 0
        assert not stale.exists()
    finally:
        site.stop()
