"""Extraction runs on plain HTML text — no Response object, no fetch stack."""

from iofast_engine.extract import CONTENT_SELECTORS, page_content, strip_noise

BODY = (
    "<h1>Getting started</h1>"
    "<p>Install the package with pip and then run the command line tool. "
    "The scraper walks a documentation site and writes one markdown file per page, "
    "keeping a ledger of every URL it enqueued so nothing fails silently.</p>"
    "<p>Every page is recorded with an outcome class, a tier, and the number of "
    "characters extracted, which makes a partial run obvious rather than invisible.</p>"
)


def test_selector_path():
    html = f"<html><body><nav>skip me</nav><main>{BODY}</main></body></html>"
    text, how = page_content(html, "https://x.com/docs/a", CONTENT_SELECTORS)
    assert how.startswith("selector"), how
    assert "Install the package" in text
    assert "skip me" not in text
    assert "<html" not in text and "<main" not in text


def test_selectorless_falls_back_to_trafilatura():
    html = f"<html><body><div class='content'>{BODY}</div></body></html>"
    text, how = page_content(html, "https://x.com/docs/b", CONTENT_SELECTORS)
    assert how == "trafilatura", how
    assert "Getting started" in text
    assert "<html" not in text and "<div" not in text


def test_never_returns_raw_body():
    text, how = page_content("<html><body></body></html>", "https://x.com/docs/c", CONTENT_SELECTORS)
    assert (text, how) == ("", "none")


def test_strip_noise_drops_chrome():
    html = "<div><nav>nav</nav><div id='breadcrumb'>bc</div><p>keep</p></div>"
    out = strip_noise(html)
    assert "keep" in out and "nav" not in out and "bc" not in out


def test_relative_links_resolved_with_trailing_slash():
    """Relative ../guides/ from /uv/getting-started/ must resolve to /uv/guides/, not /guides/."""
    html = (
        "<html><body><main>"
        '<h1>Getting started</h1>'
        '<p><a href="../guides/">Guides</a></p>'
        "</main></body></html>"
    )
    text, _ = page_content(html, "https://docs.astral.sh/uv/getting-started/")
    assert "https://docs.astral.sh/uv/guides/" in text, f"wrong link: {text}"


def test_base_href_overrides_url_for_links():
    """When <base href> is present, relative links resolve against it, not the page URL."""
    html = (
        "<html><head><base href='https://example.com/docs/'></head><body><main>"
        '<h1>Page</h1>'
        '<p><a href="setup/">Setup guide</a></p>'
        "</main></body></html>"
    )
    text, _ = page_content(html, "https://other.com/page")
    assert "https://example.com/docs/setup/" in text, f"wrong link: {text}"


def test_link_list_in_article_body_preserved():
    """A bullet list of links inside main content must survive extraction."""
    html = (
        "<html><body><main>"
        "<h1>Overview</h1>"
        "<ul>"
        '<li><a href="/install/">Installing uv</a></li>'
        '<li><a href="/first-steps/">First steps after installation</a></li>'
        '<li><a href="/features/">An overview of features</a></li>'
        '<li><a href="/help/">How to get help</a></li>'
        "</ul>"
        "<p>This paragraph comes after the list and should also be kept.</p>"
        "</main></body></html>"
    )
    text, _ = page_content(html, "https://docs.example.com/overview/")
    assert "Installing uv" in text, f"list item lost: {text}"
    assert "First steps after installation" in text, f"list item lost: {text}"
    assert "An overview of features" in text, f"list item lost: {text}"
    assert "How to get help" in text, f"list item lost: {text}"
    assert "This paragraph comes after" in text, f"prose lost: {text}"


def test_github_404_error_message(monkeypatch, tmp_path):
    """GitHub 404 must say 'repo not found or private', not 'API returned 404'."""
    import asyncio
    import urllib.error
    from unittest.mock import MagicMock
    from iofast_engine.sources.base import GrabOptions
    from iofast_engine.sources.github import GitHubSource

    src = GitHubSource()
    err = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y", 404, "Not Found", {}, None
    )
    monkeypatch.setattr("urllib.request.urlopen", MagicMock(side_effect=err))

    events = []
    result = asyncio.run(src.grab("https://github.com/x/y", tmp_path, GrabOptions(), events.append))
    assert result.failed == 1
    errors = [e for e in events if e.get("type") == "error"]
    assert errors, "no error event emitted"
    msg = errors[0]["message"]
    assert "repo not found or private" in msg.lower(), f"wrong message: {msg}"
    assert "404" in msg


def test_github_403_rate_limit_message(monkeypatch, tmp_path):
    """GitHub 403 with X-RateLimit-Reset must include the reset time."""
    import asyncio
    import urllib.error
    from email.message import Message
    from unittest.mock import MagicMock
    from iofast_engine.sources.base import GrabOptions
    from iofast_engine.sources.github import GitHubSource

    src = GitHubSource()
    headers = Message()
    headers["X-RateLimit-Reset"] = "1700000000"
    err = urllib.error.HTTPError(
        "https://api.github.com/repos/x/y", 403, "Forbidden", headers, None
    )
    monkeypatch.setattr("urllib.request.urlopen", MagicMock(side_effect=err))

    events = []
    result = asyncio.run(src.grab("https://github.com/x/y", tmp_path, GrabOptions(), events.append))
    assert result.failed == 1
    errors = [e for e in events if e.get("type") == "error"]
    assert errors, "no error event emitted"
    msg = errors[0]["message"]
    assert "rate limit" in msg.lower(), f"wrong message: {msg}"
    assert "resets" in msg.lower(), f"missing reset info: {msg}"
