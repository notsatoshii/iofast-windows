"""Single import point for async browser automation.

Returns patchright's async_playwright (bundled driver in frozen builds).
Playwright is dev-only; frozen sidecar uses patchright exclusively.
"""
from __future__ import annotations

import sys


def patch_playwright():
    """Redirect playwright.* imports to patchright.* via sys.modules.

    Scrapling's AsyncDynamicSession / AsyncStealthySession hard-import
    ``playwright.async_api`` and ``playwright.sync_api``. In frozen builds
    only patchright is bundled, so those imports fail with FileNotFoundError
    on the node driver. Patchright is API-compatible — aliasing the modules
    before scrapling loads them makes every third-party session class use
    our bundled driver transparently. Safe to call multiple times (idempotent).
    Must be called BEFORE any scrapling fetcher/session import.
    """
    if "playwright" in sys.modules and "patchright" not in sys.modules:
        return  # real playwright already loaded (dev env); don't clobber
    try:
        import patchright
        import patchright.async_api
        import patchright.sync_api
        import patchright._impl  # noqa: F401 — some code paths touch this

        sys.modules.setdefault("playwright", patchright)
        sys.modules.setdefault("playwright.async_api", patchright.async_api)
        sys.modules.setdefault("playwright.sync_api", patchright.sync_api)
        sys.modules.setdefault("playwright._impl", patchright._impl)
    except ImportError:
        pass  # patchright not installed; nothing to alias


# Run at module import time so every downstream import sees the alias.
patch_playwright()


def _patch_frozen_driver_path():
    """Fix patchright's driver resolution in frozen (PyInstaller) builds.

    In a frozen bundle, ``inspect.getfile(patchright)`` returns a path inside
    the PYZ archive (e.g. ``.../_MEIxxxxx/patchright/__init__.pyc``). The
    ``driver/`` tree with ``node.exe`` and ``package/cli.js`` is extracted as
    loose files beside the exe, NOT inside the PYZ. Patchright's own
    ``compute_driver_executable()`` therefore constructs a path that doesn't
    exist on disk → FileNotFoundError when spawning the node subprocess.

    We monkey-patch that function to resolve the driver relative to the
    *extracted* package directory (which PyInstaller places next to the exe
    under ``patchright/driver/``). This runs once at import time, before any
    browser launch.
    """
    if not getattr(sys, "frozen", False):
        return  # dev env: inspect.getfile works correctly
    try:
        import patchright._impl._driver as drv
        import patchright as _pr

        # In frozen builds sys._MEIPASS is the extraction root.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is None:
            return
        # PyInstaller 6.x places collected package data under _internal/.
        # Try both layouts for forward/backward compatibility.
        real_driver = os.path.join(meipass, "_internal", "patchright", "driver")
        if not os.path.isdir(real_driver):
            real_driver = os.path.join(meipass, "patchright", "driver")
        if not os.path.isdir(real_driver):
            return  # driver not bundled; nothing to fix

        _original = drv.compute_driver_executable

        def _fixed():
            cli_path = os.path.join(real_driver, "package", "cli.js")
            if sys.platform == "win32":
                node = os.getenv(
                    "PLAYWRIGHT_NODEJS_PATH",
                    os.path.join(real_driver, "node.exe"),
                )
            else:
                node = os.getenv(
                    "PLAYWRIGHT_NODEJS_PATH",
                    os.path.join(real_driver, "node"),
                )
            return (node, cli_path)

        drv.compute_driver_executable = _fixed
        # Also patch get_driver_env if needed — it reads version from the
        # package which should still work via PYZ, but be safe.
    except Exception:
        pass  # best-effort; unfrozen or already patched


_patch_frozen_driver_path()


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