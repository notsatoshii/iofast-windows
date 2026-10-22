"""Unit tests for browser_helper module."""
import pytest


def test_get_async_playwright_returns_patchright():
    """get_async_playwright returns patchright's async_playwright, not playwright's."""
    from iofast_engine.browser_helper import get_async_playwright

    ap = get_async_playwright()
    assert "patchright" in ap.__module__


def test_get_sync_playwright_returns_patchright():
    """get_sync_playwright returns patchright's sync_playwright, not playwright's."""
    from iofast_engine.browser_helper import get_sync_playwright

    sp = get_sync_playwright()
    # sync_playwright is a context manager class; check its module path
    assert "patchright" in sp.__module__


def test_async_playwright_callable():
    """Returned async_playwright class is callable (can instantiate)."""
    from iofast_engine.browser_helper import get_async_playwright

    ap = get_async_playwright()
    assert callable(ap)


def test_sync_playwright_callable():
    """Returned sync_playwright is usable as a context manager."""
    from iofast_engine.browser_helper import get_sync_playwright

    sp = get_sync_playwright()
    assert callable(sp)