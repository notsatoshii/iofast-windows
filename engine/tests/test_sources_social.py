"""Offline tests for Instagram and X sources using HTML fixtures.
No live network, no browser launch. We stub social_browser.open_page to return
a fake Page that serves fixture HTML, then verify each source's extraction and
markdown rendering against known-good output shapes.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import lxml.html
from lxml.cssselect import CSSSelector

from iofast_engine.sources.base import GrabOptions
from iofast_engine.sources.instagram import InstagramSource
from iofast_engine.sources.x import XSource

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def _css_select(root, selector: str):
    """Run a CSS selector against an lxml element tree using cssselect."""
    try:
        sel = CSSSelector(selector)
        return sel(root)
    except Exception:
        return []


def _css_select_one(root, selector: str):
    matches = _css_select(root, selector)
    return matches[0] if matches else None


class FakePage:
    """Minimal Playwright Page stub backed by an lxml-parsed HTML fixture."""

    def __init__(self, html_path: Path):
        self._html = html_path.read_text(encoding="utf-8")
        self._tree = lxml.html.fromstring(self._html)
        self.url = "https://example.com/fixture"

    async def wait_for_timeout(self, ms: int) -> None:  # noqa: D401
        """No-op in tests."""

    async def inner_text(self, selector: str) -> str:
        if selector == "body":
            return self._tree.text_content() or ""
        if selector == "main":
            main = self._tree.find(".//main")
            return main.text_content() if main is not None else ""
        return ""

    async def query_selector(self, selector: str):
        return _FakeElement.from_selector(self._tree, selector)

    async def query_selector_all(self, selector: str):
        return _FakeElement.all_from_selector(self._tree, selector)

    async def evaluate(self, expr: str) -> None:  # noqa: D401
        """Scroll expressions are no-ops in tests."""


class _FakeElement:
    """Wraps an lxml element with the subset of Playwright's ElementHandle API
    our extractors call."""

    def __init__(self, el):
        self._el = el

    @classmethod
    def from_selector(cls, tree, selector: str):
        el = _css_select_one(tree, selector)
        return cls(el) if el is not None else None

    @classmethod
    def all_from_selector(cls, tree, selector: str):
        return [cls(e) for e in _css_select(tree, selector)]

    async def get_attribute(self, name: str) -> str | None:
        return self._el.get(name)

    async def inner_text(self) -> str:
        return (self._el.text_content() or "").strip()

    async def query_selector(self, selector: str):
        # Handle ancestor:: axis which cssselect doesn't support.
        # Supports bare tags and simple attribute predicates like
        # ancestor::div[@attr='val'].
        if selector.startswith("ancestor::"):
            remainder = selector.split("::", 1)[1]
            m = re.match(
                r"^(\*|[a-zA-Z][\w-]*)(?:\[(@[\w-]+)='([^']*)'\])?", remainder
            )
            if m:
                tag = m.group(1)
                pred_attr = m.group(2)
                pred_val = m.group(3)
                node = self._el.getparent()
                while node is not None:
                    tag_match = tag == "*" or node.tag == tag
                    pred_match = True
                    if pred_attr and pred_val is not None:
                        pred_match = node.get(pred_attr.lstrip("@")) == pred_val
                    if tag_match and pred_match:
                        return _FakeElement(node)
                    node = node.getparent()
            return None
        child = _css_select_one(self._el, selector)
        return _FakeElement(child) if child is not None else None

    async def query_selector_all(self, selector: str):
        return [_FakeElement(e) for e in _css_select(self._el, selector)]


# ---- URL matching ----


def test_instagram_matches_post_and_profile():
    src = InstagramSource()
    assert src.matches("https://www.instagram.com/p/ABC123/")
    assert src.matches("https://instagram.com/reel/XYZ_789")
    assert src.matches("https://www.instagram.com/someuser/")
    assert not src.matches("https://x.com/testuser/status/123")


def test_x_matches_post_and_profile_both_domains():
    src = XSource()
    assert src.matches("https://x.com/user/status/123456")
    assert src.matches("https://twitter.com/user/status/123456")
    assert src.matches("https://x.com/somehandle")
    assert src.matches("https://twitter.com/somehandle/")
    assert not src.matches("https://instagram.com/p/ABC/")


# ---- Instagram extraction ----


@pytest.mark.asyncio
async def test_instagram_post_extraction(tmp_path):
    page = FakePage(FIXTURES / "instagram_post.html")
    src = InstagramSource()
    with patch(
        "iofast_engine.sources.instagram.open_page", new=AsyncMock(return_value=page)
    ), patch(
        "iofast_engine.sources.instagram.close_context", new=AsyncMock()
    ):
        result = await src.grab(
            "https://www.instagram.com/p/TESTCODE/",
            tmp_path,
            GrabOptions(),
            emit=lambda e: None,
        )
    assert result.ok == 1
    md_file = tmp_path / "instagram" / "TESTCODE.md"
    assert md_file.exists()
    text = md_file.read_text(encoding="utf-8")
    assert "--- SOURCE: https://www.instagram.com/p/TESTCODE/ ---" in text
    assert "UNTRUSTED CONTENT" in text
    assert "@testuser" in text
    assert "This is a test caption" in text
    assert "2026-10-15" in text
    assert "1,234" in text
    assert "Great photo!" in text
    assert "cdn.example.com/img.jpg" in text


@pytest.mark.asyncio
async def test_instagram_profile_extraction(tmp_path):
    page = FakePage(FIXTURES / "instagram_profile.html")
    src = InstagramSource()
    with patch(
        "iofast_engine.sources.instagram.open_page", new=AsyncMock(return_value=page)
    ), patch(
        "iofast_engine.sources.instagram.close_context", new=AsyncMock()
    ):
        result = await src.grab(
            "https://www.instagram.com/testprofile/",
            tmp_path,
            GrabOptions(limit=5),
            emit=lambda e: None,
        )
    # 3 post files + 1 index file.
    assert result.ok == 4
    idx = tmp_path / "instagram" / "testprofile-index.md"
    assert idx.exists()
    text = idx.read_text(encoding="utf-8")
    assert "@testprofile" in text
    assert "Test bio line one" in text
    assert "Latest 3 posts" in text


# ---- X extraction ----


@pytest.mark.asyncio
async def test_x_thread_extraction(tmp_path):
    """Signed-in route is used when public routes fail; verifies DOM parsing."""
    from iofast_engine.sources.social_public import PublicResult

    page = FakePage(FIXTURES / "x_thread.html")
    src = XSource()
    with patch(
        "iofast_engine.sources.x.x_fetch_syndication",
        new=AsyncMock(return_value=PublicResult(ok=False, error="HTTP 403")),
    ), patch(
        "iofast_engine.sources.x.x_fetch_oembed",
        new=AsyncMock(return_value=PublicResult(ok=False, error="HTTP 403")),
    ), patch(
        "iofast_engine.sources.x.open_page", new=AsyncMock(return_value=page)
    ), patch(
        "iofast_engine.sources.x.close_context", new=AsyncMock()
    ):
        result = await src.grab(
            "https://x.com/testuser/status/1111111111111111111",
            tmp_path,
            GrabOptions(),
            emit=lambda e: None,
        )
    assert result.ok == 1
    md_file = tmp_path / "x" / "thread-1111111111111111111.md"
    assert md_file.exists()
    text = md_file.read_text(encoding="utf-8")
    assert "UNTRUSTED CONTENT" in text
    assert "First post in the thread" in text
    assert "Second post continuing" in text
    assert "quoted_user" in text
    assert "This is a quoted tweet" in text
    assert "pbs.twimg.com/media/a.jpg" in text


@pytest.mark.asyncio
async def test_x_profile_extraction(tmp_path):
    page = FakePage(FIXTURES / "x_profile.html")
    src = XSource()
    with patch(
        "iofast_engine.sources.x.open_page", new=AsyncMock(return_value=page)
    ), patch(
        "iofast_engine.sources.x.close_context", new=AsyncMock()
    ):
        result = await src.grab(
            "https://x.com/testuser",
            tmp_path,
            GrabOptions(limit=5),
            emit=lambda e: None,
        )
    # 2 post files + 1 index file.
    assert result.ok == 3
    idx = tmp_path / "x" / "testuser-index.md"
    assert idx.exists()
    text = idx.read_text(encoding="utf-8")
    assert "@testuser" in text
    assert "Test user bio for fixture" in text
    assert "Latest 2 posts" in text


# ---- Wall detection ----


@pytest.mark.asyncio
async def test_instagram_wall_writes_error_when_all_routes_fail(tmp_path):
    """When public routes fail AND signed-in browser hits a wall, we write an
    error markdown file instead of raising — the caller sees failed=1."""
    from iofast_engine.sources.social_browser import SocialWallError
    from iofast_engine.sources.social_public import PublicResult

    src = InstagramSource()
    with patch(
        "iofast_engine.sources.instagram.ig_fetch_post_ytdlp",
        new=AsyncMock(return_value=PublicResult(ok=False, error="yt-dlp failed")),
    ), patch(
        "iofast_engine.sources.instagram.ig_fetch_embed",
        new=AsyncMock(return_value=PublicResult(ok=False, error="HTTP 403")),
    ), patch(
        "iofast_engine.sources.instagram.open_page",
        new=AsyncMock(side_effect=SocialWallError("login wall")),
    ):
        result = await src.grab(
            "https://www.instagram.com/p/WALLED/",
            tmp_path,
            GrabOptions(),
            emit=lambda e: None,
        )
    assert result.ok == 0
    assert result.failed == 1
    md_file = tmp_path / "instagram" / "WALLED.md"
    assert md_file.exists()
    text = md_file.read_text(encoding="utf-8")
    assert "Sign-in is required" in text


# ---- RPC handlers ----


def test_social_rpc_status_returns_dict():
    from iofast_engine.sources.social_rpc import status

    with patch(
        "iofast_engine.sources.social_rpc._run_async",
        return_value={"instagram": False, "x": False},
    ):
        result = status({}, emit=lambda e: None)
    assert isinstance(result, dict)
    assert "instagram" in result
    assert "x" in result


def test_social_rpc_signin_validates_site():
    from iofast_engine.sources.social_rpc import signin

    with pytest.raises(ValueError, match="site must be"):
        signin({"site": "tiktok"}, emit=lambda e: None)