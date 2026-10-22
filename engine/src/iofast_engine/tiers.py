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
    """Check whether any browser can be launched for dynamic/stealth tiers.

    Tries system Edge/Chrome first (no download needed), then falls back to
    managed Chromium. In frozen builds, the patchright driver is bundled so
    system channels always work when Edge or Chrome is installed. Unfrozen
    dev environments also check managed Chromium as a fallback.
    """
    try:
        from .browser_helper import get_sync_playwright

        sync_playwright = get_sync_playwright()
        with sync_playwright() as pw:
            # System browsers first — no download needed.
            for channel in ("msedge", "chrome"):
                try:
                    browser = pw.chromium.launch(channel=channel, headless=True)
                    browser.close()
                    return True
                except Exception:
                    continue
            # Managed Chromium fallback (dev environments with `playwright install`).
            try:
                browser = pw.chromium.launch(headless=True)
                browser.close()
                return True
            except Exception:
                return False
    except Exception:
        return False
