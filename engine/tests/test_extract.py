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
