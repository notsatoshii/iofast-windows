"""Single import point for async browser automation.

Returns patchright's async_playwright (bundled driver in frozen builds).
Playwright is dev-only; frozen sidecar uses patchright exclusively.
"""
from __future__ import annotations


def get_async_playwright():
    """Return patchright's async_playwright class.

    Single import point so every browser-launching module uses the same
    backend. Patchright ships its node driver in the frozen bundle;
    playwright's driver is stripped (dead weight — same API).
    """
    from patchright.async_api import async_playwright

    return async_playwright


def get_sync_playwright():
    """Return patchright's sync_playwright context manager.

    Used by tiers.browsers_available() which runs sync playwright inside
    an executor thread (sync playwright cannot run on an asyncio loop).
    """
    from patchright.sync_api import sync_playwright

    return sync_playwright