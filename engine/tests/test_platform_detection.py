"""Detection lands late when the first page is a challenge — and selectors follow it."""

from pathlib import Path

from iofast_engine.adapters.detection import detect_late, selectors_for
from iofast_engine.adapters.gitbook import ADAPTER as GITBOOK
from iofast_engine.extract import CONTENT_SELECTORS
from iofast_engine.spider import DocSpider

FIXTURES = Path(__file__).parent / "fixtures"
CHALLENGE = FIXTURES / "negatives/cloudflare-challenge.html"
GITBOOK_PAGE = FIXTURES / "platforms/gitbook/static-blocks.html"


def bare_spider():
    """A DocSpider without __init__ — the late-detection path needs no network."""
    spider = DocSpider.__new__(DocSpider)
    spider.events = []
    spider.emit = spider.events.append
    spider.adapter = None
    spider.selectors = CONTENT_SELECTORS
    return spider


def test_a_challenge_page_detects_nothing():
    assert (
        detect_late(
            "https://docs.gitbook.com/",
            403,
            {"cf-mitigated": "challenge"},
            CHALLENGE.read_text(),
        )
        is None
    )


def test_platform_lands_on_the_first_real_page():
    spider = bare_spider()

    spider._detect_late("https://docs.gitbook.com/", 403, {}, CHALLENGE.read_text())
    assert spider.adapter is None, "a challenge must not set a platform"

    spider._detect_late(
        "https://docs.gitbook.com/creating-content/blocks", 200, {}, GITBOOK_PAGE.read_text()
    )
    assert spider.adapter is GITBOOK
    assert [e["type"] for e in spider.events] == ["platform"]
    assert spider.events[0]["platform"] == "gitbook"


def test_content_selector_is_prepended_not_replaced():
    from iofast_engine.adapters.base import Adapter

    adapter = Adapter(name="x", detect=lambda fp: False, content_selector="div.doc-body")
    assert selectors_for(adapter, CONTENT_SELECTORS) == ("div.doc-body",) + CONTENT_SELECTORS
    assert selectors_for(None, CONTENT_SELECTORS) == CONTENT_SELECTORS
    assert selectors_for(GITBOOK, CONTENT_SELECTORS) == CONTENT_SELECTORS
