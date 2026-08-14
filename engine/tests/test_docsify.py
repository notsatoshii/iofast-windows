"""Docsify harvest. Offline: every fetch is served from the committed fixtures.

The live shapes these cases stand in for (measured 2026-08-14):
  docsify.js.org            v5, default README.md homepage, 15 sidebar entries
  TracyDebugger             v4, homepage: 'introduction.md', flat sidebar
  libocca.org               v4, root _sidebar.md is EMPTY -> homepage only
  medusa-extender           v4, sidebar is anchor-only -> homepage only
"""

from pathlib import Path

import pytest

from iofast_engine.adapters import run
from iofast_engine.adapters.detection import fingerprint
from iofast_engine.adapters.docsify import ADAPTER, LINK_RE, _targets

FIXTURES = Path(__file__).parent / "fixtures/platforms/docsify"
SHELL = (FIXTURES / "static-landing.html").read_text()
TRACY = (FIXTURES / "static-tracy-landing.html").read_text()
SIDEBAR = (FIXTURES / "_sidebar.md").read_text()
QUICKSTART = (FIXTURES / "quickstart.md").read_text()
ROOT = "https://docsify.js.org/"
MD_HEADERS = {"content-type": "text/markdown; charset=utf-8"}

# What the committed shell + _sidebar.md imply, written out rather than computed:
# a list derived from the code under test would accept whatever that code produced.
# `awesome.md` / `changelog.md` are absent on purpose — the shell aliases both to
# raw.githubusercontent, and both really do 404 on the live site (2026-08-14).
EXPECTED = [ROOT + name for name in (
    "README.md", "quickstart.md", "adding-pages.md", "cover.md", "custom-navbar.md",
    "configuration.md", "themes.md", "plugins.md", "write-a-plugin.md", "markdown.md",
    "language-highlight.md", "emoji.md", "deploy.md", "helpers.md", "vue.md", "cdn.md",
    "pwa.md", "embed-files.md", "ui-kit.md", "v5-upgrade.md",
)]


class FakeResponse:
    def __init__(self, text, status=200, headers=None):
        self.body = text.encode()
        self.status = status
        self.headers = headers if headers is not None else MD_HEADERS


def serve(pages, calls=None):
    """A get() over {url: FakeResponse}; anything else is a 404 HTML body."""

    def fake_get(url, timeout=20, headers=None):
        if calls is not None:
            calls.append(url)
        return pages.get(url, FakeResponse("<html>404</html>", 404, {"content-type": "text/html"}))

    return fake_get


def site(extra=None, sidebar=SIDEBAR, shell=SHELL, root=ROOT, targets=EXPECTED):
    """Every URL the adapter should ask for, served as real markdown."""
    pages = {
        root: FakeResponse(shell, headers={"content-type": "text/html"}),
        root + "_sidebar.md": FakeResponse(sidebar),
    }
    for url in targets:
        pages[url] = FakeResponse(QUICKSTART)
    pages.update(extra or {})
    return pages


def test_detect_fires_on_both_committed_shells():
    for html, url in ((SHELL, ROOT), (TRACY, "https://adrianbj.github.io/TracyDebugger/")):
        assert ADAPTER.detect(fingerprint(url, 200, {}, html)) is True


def test_detect_needs_the_script_not_just_the_config_word():
    """A page that merely writes about window.$docsify is not a docsify site."""
    prose = '<html><body><pre>window.$docsify = { loadSidebar: true }</pre></body></html>'
    assert ADAPTER.detect(fingerprint("https://blog.example.com/p", 200, {}, prose)) is False


def test_harvest_reads_the_sidebar_and_never_writes_the_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(run, "api_get", serve(site(), calls))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, ROOT, str(tmp_path))

    assert written == len(EXPECTED), "the sidebar entries are pages, not one shell"
    assert manifest["reconciled"]
    assert manifest["info"]["discovery"] == "api (docsify)"
    assert calls[2:] == EXPECTED, "exactly the pages the committed sidebar implies"
    assert ROOT not in ledger.enqueued, "the shell is read for config, never written as a page"
    assert ROOT + "awesome.md" not in ledger.enqueued, "aliased off-site: not a page of this run"
    assert set(ledger.enqueued) == set(ledger.records)
    assert all(record["outcome"] == "ok" for record in ledger.records.values())
    assert calls[0] == ROOT and calls[1] == ROOT + "_sidebar.md", "config, then nav, then pages"


def test_configured_homepage_wins_over_readme(tmp_path, monkeypatch):
    """TracyDebugger sets homepage: 'introduction.md' — README.md would 404 there."""
    root = "https://adrianbj.github.io/TracyDebugger/"
    pages = {
        root: FakeResponse(TRACY, headers={"content-type": "text/html"}),
        root + "_sidebar.md": FakeResponse("- [Introduction](introduction.md)\n"),
        root + "introduction.md": FakeResponse("# Tracy\n\n" + "body text. " * 40),
    }
    monkeypatch.setattr(run, "api_get", serve(pages))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, root, str(tmp_path))

    assert written == 1
    assert set(ledger.enqueued) == {root + "introduction.md"}
    assert root + "README.md" not in ledger.enqueued
    assert manifest["reconciled"]


def test_the_shell_can_never_point_the_run_off_the_site():
    """Every URL in the shell is scraped content, i.e. untrusted input.

    Each of these resolved to a foreign host or above the root before the
    containment gate existed (measured 2026-08-14).
    """
    hostile = [
        ("window.$docsify={basePath:'//evil.example.com/x/'}", "- [a](a.md)\n", ROOT),
        ("window.$docsify={basePath:'http://169.254.169.254/latest/'}", "- [a](a.md)\n", ROOT),
        ("window.$docsify={homepage:'https://raw.githubusercontent.com/o/r/main/README.md'}", "", ROOT),
        ("window.$docsify={homepage:'../../../secret.md'}", "", "https://a.github.io/proj/"),
    ]
    for shell, sidebar, root in hostile:
        for target in _targets(shell, sidebar, root):
            assert target.startswith(root), f"{shell or sidebar} escaped to {target}"


def test_percent_encoded_traversal_is_dropped_not_just_prefix_checked():
    """`%2E%2E/` keeps the root prefix intact, so startswith() alone would pass it —
    yet servers that decode before resolving the path would climb out of the root."""
    root = "https://a.github.io/proj/"
    assert _targets("", "- [X](%2E%2E/%2E%2E/a.md)\n- [Y](ok.md)\n", root) == [
        root + "README.md",
        root + "ok.md",
    ]


def test_a_commented_out_option_is_not_config():
    """docsify.js.org's own shell keeps half its options commented out."""
    shell = "window.$docsify = {\n  // basePath: '/wrong/',\n  loadSidebar: true,\n}"
    assert _targets(shell, "- [a](a.md)\n", ROOT) == [ROOT + "README.md", ROOT + "a.md"]


def test_an_mdx_link_is_not_truncated_into_an_md_url():
    """`guide.mdx` captured as `guide.md` would fetch a URL that never existed."""
    assert LINK_RE.findall("- [X](guide.mdx)\n- [Y](ok.md)\n") == ["ok.md"]


def test_base_path_moves_the_whole_run():
    shell = "<script src='//unpkg.com/docsify@4/lib/docsify.min.js'></script>" \
            "<script>window.$docsify = { basePath: '/docs/', loadSidebar: true }</script>"
    targets = _targets(shell, "- [Quick start](quickstart.md)\n", ROOT)
    assert targets == [ROOT + "docs/README.md", ROOT + "docs/quickstart.md"]


def test_off_site_and_out_of_root_entries_are_not_pages():
    """medusa-extender's sidebar is anchor-only; a link may point off-host or above the root.

    The root here is a project subdirectory (GitHub Pages), which is what makes the
    `../../` escape observable at all — at an origin root there is nothing above it.
    """
    root = "https://adrianbj.github.io/TracyDebugger/"
    sidebar = (
        "- [Introduction](#introduction)\n"
        "- [External](https://example.com/other.md)\n"
        "- [Protocol relative](//evil.example.com/x.md)\n"
        "- [Parent escape](../../elsewhere/x.md)\n"
        "- [Real page](guide/install.md)\n"
    )
    assert _targets(TRACY, sidebar, root) == [root + "introduction.md", root + "guide/install.md"]


def test_a_missing_page_is_recorded_not_swallowed(tmp_path, monkeypatch):
    pages = site()
    del pages[ROOT + "quickstart.md"]  # 404 HTML, like a stale sidebar entry
    monkeypatch.setattr(run, "api_get", serve(pages))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, ROOT, str(tmp_path))

    assert manifest["reconciled"], "a 404 page must not break reconcile"
    assert ledger.records[ROOT + "quickstart.md"]["outcome"] == "failed_fetch"
    assert ledger.records[ROOT + "quickstart.md"]["status"] == 404
    assert written >= 1


def test_an_html_body_answered_as_markdown_is_refused(tmp_path, monkeypatch):
    """A docsify 404 handler that answers 200 text/markdown with HTML is not a page."""
    pages = site(extra={ROOT + "quickstart.md": FakeResponse("<html>nope</html>" * 40)})
    monkeypatch.setattr(run, "api_get", serve(pages))
    ledger, _, _, manifest = run.harvest_run(ADAPTER, ROOT, str(tmp_path))

    assert ledger.records[ROOT + "quickstart.md"]["outcome"] == "failed_fetch"
    assert ledger.records[ROOT + "quickstart.md"]["reason"] == "not markdown"
    assert manifest["reconciled"]


def test_empty_sidebar_still_yields_the_homepage(tmp_path, monkeypatch):
    """libocca.org's root _sidebar.md is empty — the run is the homepage, not nothing."""
    monkeypatch.setattr(run, "api_get", serve(site(sidebar="")))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, ROOT, str(tmp_path))

    assert written == 1
    assert set(ledger.enqueued) == {ROOT + "README.md"}
    assert manifest["reconciled"]


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        ("https://docsify.js.org/", "https://docsify.js.org/"),
        ("https://docsify.js.org", "https://docsify.js.org/"),
        ("https://docsify.js.org/#/quickstart", "https://docsify.js.org/"),
        ("https://adrianbj.github.io/TracyDebugger", "https://adrianbj.github.io/TracyDebugger/"),
        ("https://adrianbj.github.io/TracyDebugger/index.html", "https://adrianbj.github.io/TracyDebugger/"),
        # A dotted DIRECTORY is a path segment, not a shell filename: stripping it
        # would move the run to the origin, i.e. a different site entirely.
        ("https://mrdoob.github.io/three.js", "https://mrdoob.github.io/three.js/"),
    ],
)
def test_the_root_is_the_directory_the_shell_lives_in(start, expected):
    from iofast_engine.adapters.docsify import _dir_url

    assert _dir_url(start) == expected


def test_max_pages_stops_the_harvest(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "api_get", serve(site()))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, ROOT, str(tmp_path), max_pages=3)

    assert written == 3
    assert manifest["reconciled"], "pages past the cap are never enqueued"
