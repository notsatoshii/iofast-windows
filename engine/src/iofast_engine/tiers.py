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


# Body markers. The script src is the sturdiest of the three — Cloudflare can
# rebrand the interstitial's copy (a live custom-branded one carried no "just a
# moment..." at all) but the challenge still has to load /cdn-cgi/challenge-platform/.
# Checked against Cloudflare's OWN challenge documentation (2026-08-13): those pages
# describe challenges in prose and contain none of these literals, so scraping the
# docs that explain the mechanism does not read as the mechanism.
# Only the interstitial's own script trees count: /h/<x>/orchestrate/... today and the
# older /orchestrate/jsch/... The bare prefix also covers Bot Management's per-page
# scripts (/scripts/jsd/main.js on every normal page of developer.quickintel.io,
# 2026-10-21), which are not challenges.
CHALLENGE_MARKERS = (
    "just a moment...",
    "cf_chl_opt",
    "/cdn-cgi/challenge-platform/h/",
    "/cdn-cgi/challenge-platform/orchestrate/",
)
# Measured 2026-08-13: a live custom-branded challenge body was 27674 bytes with its
# first marker at 14866 — invisible to the 8000-char window this replaces. Real docs
# pages measured 105-208KB, so the cap still avoids lowercasing a whole document;
# it just has to clear any plausible challenge page.
CHALLENGE_SCAN_CHARS = 65536


def is_challenge(status: int, headers: dict, text: str) -> bool:
    """Cloudflare interstitial detection (header + body markers).

    `status` is part of the signature so adapters can key on it later; the
    detection itself is header+body only, exactly as before.

    The body markers carry this alone whenever the caller has no headers to pass —
    solve.py's headed poll loop reads a live page, and a page cannot see its own
    response headers. That path is why the scan window has to clear the marker.
    """
    if "challenge" in header_lookup(headers, "cf-mitigated").lower():
        return True
    head = text[:CHALLENGE_SCAN_CHARS].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


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
