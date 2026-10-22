"""Dedicated persistent browser profile for signed-in social captures.

Hard rules (Eric's decision): no stealth/fingerprint patches, no CAPTCHA solving,
no automated sign-in, no private APIs. We launch the user's installed browser
(Edge first, then Chrome) via Playwright `channel`, pointed at a persistent
profile the user signs into once by hand. Captures run headless on that same
profile; if headless hits a wall we retry once headed (minimized) and otherwise
fail with the reason.

Profile location:
  Windows: %LOCALAPPDATA%/iofast/social-profile
  macOS:   ~/Library/Application Support/iofast/social-profile
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Lazy-imported at runtime so tests can stub playwright without installing it.
_playwright = None


def _profile_dir() -> Path:
    """One persistent profile, shared across signin + capture runs."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "iofast" / "social-profile"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "iofast" / "social-profile"
    return Path.home() / ".local" / "share" / "iofast" / "social-profile"


def _channel_preference() -> list[str]:
    """Prefer Edge on Windows (ships with the OS), Chrome everywhere else."""
    if sys.platform == "win32":
        return ["msedge", "chrome"]
    return ["chrome", "msedge"]


async def _launch(*, headless: bool, minimized: bool = False):
    """Launch a persistent-context browser on the social profile.

    Returns (playwright_instance, context). Caller owns both lifetimes.
    Tries each channel in preference order; raises on total failure.
    """
    global _playwright
    if _playwright is None:
        from ..browser_helper import get_async_playwright

        _ap = get_async_playwright()
        _playwright = await _ap().start()

    profile = _profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    last_err: Optional[Exception] = None
    for channel in _channel_preference():
        try:
            ctx = await _playwright.chromium.launch_persistent_context(
                str(profile),
                channel=channel,
                headless=headless,
                args=["--start-minimized"] if (headless is False and minimized) else [],
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                ignore_https_errors=True,
            )
            return _playwright, ctx
        except Exception as exc:  # noqa: BLE001 — channel not installed
            last_err = exc
            continue
    raise RuntimeError(f"no usable browser channel found: {last_err}")


_LOGIN_URLS = {
    "instagram": "https://www.instagram.com/accounts/login/",
    "x": "https://x.com/i/flow/login",
}

_SESSION_MARKERS = {
    # Cookie names whose presence means "signed in" for status() checks.
    "instagram": {"sessionid", "ds_user_id"},
    "x": {"auth_token", "ct0"},
}


async def signin(site: str) -> dict:
    """Open a VISIBLE browser on the site's login page; resolve when closed.

    Returns {"signed_in": bool}. The user closes the window when done; we do
    not automate any part of sign-in.
    """
    if site not in _LOGIN_URLS:
        raise ValueError(f"unknown social site: {site}")

    pw, ctx = await _launch(headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(_LOGIN_URLS[site], wait_until="domcontentloaded")
        # Block until the user closes the window. playwright emits 'close' on
        # the context when the last page is gone.
        close_event = asyncio.Event()
        ctx.on("close", lambda: close_event.set())
        await close_event.wait()
    finally:
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass

    # Re-open briefly (headless) to read cookies without disturbing the user.
    return await status()


async def status() -> dict:
    """Report signed-in state per site WITHOUT opening a visible window.

    Reads cookies from the persistent profile by launching headless, checking
    for the session marker cookies, then closing immediately.
    """
    result: dict[str, bool] = {"instagram": False, "x": False}
    try:
        pw, ctx = await _launch(headless=True)
    except RuntimeError:
        # No browser installed yet — definitely not signed in.
        return result
    try:
        cookies = await ctx.cookies()
        by_domain: dict[str, set[str]] = {}
        for c in cookies:
            host = c.get("domain", "").lstrip(".")
            by_domain.setdefault(host, set()).add(c["name"])

        for site, markers in _SESSION_MARKERS.items():
            # Match any cookie domain that ends with the site's root domain.
            roots = {"instagram": "instagram.com", "x": "x.com"}
            root = roots.get(site, f"{site}.com")
            matched_domains = [d for d in by_domain if d.endswith(root)]
            names = set().union(*(by_domain[d] for d in matched_domains))
            result[site] = bool(markers & names)
    finally:
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass
    return result


class SocialWallError(Exception):
    """Raised when a capture hits a login wall, challenge, or rate limit."""


_WALL_SIGNALS = (
    # Substrings in page text or URL that indicate a non-content page.
    "log in to instagram",
    "login to instagram",
    "please log in",
    "sign in to x",
    "login to x",
    "access denied",
    "rate limit",
    "too many requests",
    "just a moment",          # Cloudflare-style challenge
    "checking your browser",
)


async def _check_wall(page) -> Optional[str]:
    """Return a reason string if the page looks like a wall, else None."""
    url = page.url.lower()
    # Login redirects are the most common wall signal.
    if "/accounts/login" in url or "/i/flow/login" in url:
        return "login wall (redirected to sign-in)"
    try:
        text = (await page.inner_text("body"))[:4000].lower()
    except Exception:  # noqa: BLE001
        text = ""
    for signal in _WALL_SIGNALS:
        if signal in text:
            return f"wall detected: {signal}"
    return None


async def open_page(url: str, *, emit=None):
    """Navigate to url on the persistent profile, headless first.

    On a login wall or challenge, retry once headed (minimized). Returns the
    Page object on success; raises SocialWallError with the reason on failure.
    Caller owns the returned page's context lifetime (use ``await close_context(page)``).
    """
    emit = emit or (lambda event: None)

    async def _try(headless: bool, minimized: bool = False):
        pw, ctx = await _launch(headless=headless, minimized=minimized)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # Human-speed pause so the page finishes lazy-loaded content.
        await page.wait_for_timeout(1500)
        reason = await _check_wall(page)
        if reason is not None:
            await ctx.close()
            await pw.stop()
            return None, reason
        return page, None

    # Attempt 1: headless (fast, quiet).
    page, reason = await _try(headless=True)
    if page is not None:
        return page

    emit({"type": "warning", "url": url, "message": f"headless hit wall ({reason}); retrying headed"})

    # Attempt 2: headed but minimized, so the user sees activity but isn't blocked.
    page, reason2 = await _try(headless=False, minimized=True)
    if page is not None:
        return page

    raise SocialWallError(reason2 or reason or "unknown wall")


async def close_context(page):
    """Close the page's browser context and stop playwright. Safe to call twice."""
    try:
        ctx = page.context
        pw = ctx.browser_type.playwright if hasattr(ctx, "browser_type") else None
        await ctx.close()
        if pw is not None:
            await pw.stop()
    except Exception:  # noqa: BLE001
        pass