"""Escalation ladder + challenge detection. Plain data in — no Response objects."""

import os

TIERS = ("static", "dynamic", "stealth")
# Escalation trigger, recalibrated against the committed fixture matrix (2026-08-11).
# The bands overlap now: shells extract up to 218 chars (document360 static) while
# real pages go down to 132 (mintlify's nav-only landing) — no threshold separates
# them cleanly. 250 = max measured shell + margin, biased UP on purpose: a false
# escalation is lossless (best-tier memory keeps the richest output) while a shell
# that clears the bar ships garbage. Small real pages just cost one browser fetch.
THIN_CHARS = 250


def header_lookup(headers: dict, name: str) -> str:
    for key, value in (headers or {}).items():
        if key.lower() == name:
            return str(value)
    return ""


def is_challenge(status: int, headers: dict, text: str) -> bool:
    """Cloudflare interstitial detection (header + body markers).

    `status` is part of the signature so adapters can key on it later; the
    detection itself is header+body only, exactly as before.
    """
    if "challenge" in header_lookup(headers, "cf-mitigated").lower():
        return True
    head = text[:8000].lower()
    return "just a moment..." in head or "cf_chl_opt" in head


def browsers_available() -> bool:
    """Dynamic/stealth tiers need this Playwright build's own chromium on disk.

    Ask Playwright for the executable path it would use: a cache holding some other
    chromium revision is a false positive (`python -m playwright install chromium` fixes it).
    Frozen builds strip the node driver; components download out-of-band into app
    support (browsers.py) — until they exist, answer False without spawning anything.
    """
    import sys

    from . import browsers as components

    if getattr(sys, "frozen", False) and not components.drivers_installed():
        return False
    components.activate()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return os.path.exists(pw.chromium.executable_path)
    except Exception:
        return False
