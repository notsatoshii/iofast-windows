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


def fetch_with_browser(url: str, *, timeout_ms: int = 15000) -> tuple[str | None, str]:
    """Fetch a URL using patchright (system browser first, managed fallback).

    Returns (html_content, info_string) where info describes what happened:
    - ("html...", "system msedge") on success with system browser
    - ("html...", "managed chromium") on success with managed browser
    - (None, "error: <message>") on failure

    Runs synchronously — designed for use inside executor threads.
    Never raises; all errors are captured in the info string.
    """
    try:
        sync_playwright = get_sync_playwright()
        with sync_playwright() as pw:
            # Try system browsers first (no download needed)
            for channel in ("msedge", "chrome"):
                try:
                    browser = pw.chromium.launch(
                        channel=channel,
                        headless=True,
                        args=["--headless=new"],
                    )
                    try:
                        page = browser.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        # Wait for network to settle so JS-rendered content appears
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass  # networkidle timeout is non-fatal
                        html = page.content()
                        return html, f"system {channel}"
                    finally:
                        browser.close()
                except Exception:
                    continue

            # Fall back to managed chromium
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                html = page.content()
                return html, "managed chromium"
            finally:
                browser.close()
    except Exception as e:
        return None, f"error: {type(e).__name__}: {e}"


async def async_fetch_with_browser(
    url: str, *, timeout_ms: int = 15000
) -> tuple[str | None, str]:
    """Async version of fetch_with_browser for use in async contexts.

    Returns (html_content, info_string). Never raises.
    """
    try:
        ap = get_async_playwright()
        pw = await ap().start()
        try:
            for channel in ("msedge", "chrome"):
                try:
                    browser = await pw.chromium.launch(
                        channel=channel,
                        headless=True,
                        args=["--headless=new"],
                    )
                    try:
                        page = await browser.new_page()
                        await page.goto(
                            url, wait_until="domcontentloaded", timeout=timeout_ms
                        )
                        try:
                            await page.wait_for_load_state(
                                "networkidle", timeout=5000
                            )
                        except Exception:
                            pass
                        html = await page.content()
                        return html, f"system {channel}"
                    finally:
                        await browser.close()
                except Exception:
                    continue

            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                html = await page.content()
                return html, "managed chromium"
            finally:
                await browser.close()
        finally:
            await pw.stop()
    except Exception as e:
        return None, f"error: {type(e).__name__}: {e}"