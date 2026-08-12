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

from iofast_engine.extract import CONTENT_SELECTORS, page_content
from iofast_engine.ledger import FAILURE_OUTCOMES, OUTCOMES
from iofast_engine.runner import run_scrape
from iofast_engine.spider import UPDATE_SHRINK_RATIO
from iofast_engine.tiers import THIN_CHARS

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
            # report.md + README-UNTRUSTED.md are run metadata, regenerated every run
            if name.endswith(".md") and name not in ("report.md", "README-UNTRUSTED.md")
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


# A soft-block/consent page: answers 200, no Cloudflare markers, and extracts
# past THIN_CHARS — so nothing upstream of the overwrite guard rejects it.
SOFT_BLOCK = """<!doctype html><html><head><title>One more step</title></head><body>
<main><h1>Before you continue</h1>
<p>We value your privacy and your reading experience. Please review our terms
of service and confirm your consent preferences before you continue to the
requested documentation page. Click accept to proceed with all cookies
enabled, or open the preference manager to make a granular choice. Your
selection is stored for one year and can be changed at any time.</p>
</main></body></html>"""


def _extracted(html, url):
    return len(page_content(html, url, CONTENT_SELECTORS)[0])


def test_suspect_shrink_outcome_is_a_failure_class():
    assert "suspect_shrink" in OUTCOMES
    assert "suspect_shrink" in FAILURE_OUTCOMES  # exit 1 flags the unattended run


@pytest.mark.integration
def test_update_refuses_drastic_shrink_keeps_file(tmp_path):
    """MUST-REFUSE: a consent page replacing a real doc page on an update run
    is not written; the previous file, chars, and validators stay the truth."""
    site = Site()
    big = PAGE.format(title="Guide", body="guide v1 content " * 100)
    site.bodies["/docs/guide"] = big
    # Fixture preconditions: the shrink must genuinely clear every upstream
    # filter (thin band) AND sit well inside the refuse band, else this test
    # passes for the wrong reason.
    prev_chars = _extracted(big, "http://x/docs/guide")
    soft_chars = _extracted(SOFT_BLOCK, "http://x/docs/guide")
    assert soft_chars >= THIN_CHARS, "fixture bug: soft block would be thin-escalated"
    assert soft_chars < prev_chars * (UPDATE_SHRINK_RATIO - 0.05), "fixture bug: shrink too mild"
    try:
        first = scrape(site, tmp_path)
        assert first["exit_code"] == 0, first["manifest"]["counts"]
        rows = {r["url"]: r for r in first["manifest"]["pages"]}
        guide_url = next(u for u in rows if u.endswith("/docs/guide"))
        assert rows[guide_url]["chars"] == prev_chars
        guide_file = rows[guide_url]["file"]
        path = tmp_path / guide_file
        bytes_before = path.read_bytes()

        site.bodies["/docs/guide"] = SOFT_BLOCK
        site.etags["/docs/guide"] = '"soft-block"'
        second = scrape(site, tmp_path, update=True)
        assert second["exit_code"] == 1  # suspect page = completed with failures
        pages = {r["url"]: r for r in second["manifest"]["pages"]}
        guide = pages[guide_url]
        assert guide["outcome"] == "suspect_shrink"
        assert guide["file"] == guide_file
        assert guide["chars"] == prev_chars  # the file on disk is still the old one
        assert "kept previous file" in guide["reason"]
        assert path.read_bytes() == bytes_before, "guard must not touch the file"
        # Old validators survive so the NEXT update re-fetches and re-judges.
        assert guide["etag"] == '"guide-v1"'
        # The unchanged page still 304s — the guard is per-page, not per-run.
        home = next(r for u, r in pages.items() if u.endswith("/docs"))
        assert home["reason"] == "not modified (304)"
    finally:
        site.stop()


@pytest.mark.integration
def test_update_allows_honest_plausible_shrink(tmp_path):
    """MUST-PASS: an honest editorial trim (above the ratio) overwrites normally."""
    site = Site()
    big = PAGE.format(title="Guide", body="guide v1 content " * 100)
    honest = PAGE.format(title="Guide", body="guide v2 rewritten shorter " * 45)
    site.bodies["/docs/guide"] = big
    prev_chars = _extracted(big, "http://x/docs/guide")
    honest_chars = _extracted(honest, "http://x/docs/guide")
    assert honest_chars < prev_chars, "fixture bug: not a shrink at all"
    assert honest_chars > prev_chars * (UPDATE_SHRINK_RATIO + 0.05), "fixture bug: trim too deep"
    try:
        first = scrape(site, tmp_path)
        assert first["exit_code"] == 0, first["manifest"]["counts"]
        site.bodies["/docs/guide"] = honest
        site.etags["/docs/guide"] = '"guide-v2"'
        second = scrape(site, tmp_path, update=True)
        assert second["exit_code"] == 0
        pages = {r["url"]: r for r in second["manifest"]["pages"]}
        guide = next(r for u, r in pages.items() if u.endswith("/docs/guide"))
        assert guide["outcome"] == "ok"
        assert guide["chars"] == honest_chars
        assert guide["etag"] == '"guide-v2"'
        body = (tmp_path / guide["file"]).read_text(encoding="utf-8")
        assert "guide v2 rewritten shorter" in body
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
