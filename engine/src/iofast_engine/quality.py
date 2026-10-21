"""Scrape-quality verdict: is what landed real prose, or a mangled nav shell?

Complements `clean.py`, which answers a different question. Clean asks whether
every enqueued URL produced a file; quality asks whether those files are worth
having. A run can be perfectly clean and still be 30 copies of a sidebar.

The flags below drove the 2026-08-13 doc-site-shape audit and back the committed
discovery constants, so the logic is pinned by tests and must not drift. Stdlib
only — `writer` imports this, and `tests/test_imports.py` keeps that path pure.
"""

import statistics

THIN_FLOOR = 800  # chars; below this an "ok" doc page is suspect
HTML_REASONS = {"selector+trafilatura", "selector+markdownify", "trafilatura"}


def analyse(manifest: dict) -> dict:
    pages = manifest.get("pages", [])
    # thin_escalated_ok = written after a browser-tier rescue; still an extracted page
    ok = [p for p in pages if p.get("outcome") in ("ok", "thin_escalated_ok")]
    chars = [p["chars"] for p in ok]
    thin = [p for p in ok if p["chars"] < THIN_FLOOR]
    suspect = [p for p in thin if p.get("reason") in HTML_REASONS]
    md_twin = [p for p in ok if p.get("reason") == "llms.txt .md variant"]
    # The other side of the same counter: a crawl that fell back to scraping HTML
    # on every page reads identically to a healthy one without it.
    html_fallback = [p for p in ok if p.get("reason") in HTML_REASONS]

    clean = manifest.get("clean", {})
    link_ratio = clean.get("link_ratio", {})

    # Duplicates count here: a site serving one block/shell page for every URL
    # dedupes to a single file, and must still read as uniform tiny pages.
    dups = [p for p in pages if p.get("outcome") == "duplicate"]
    sizes = chars + [p["chars"] for p in dups]
    uniform_tiny = False
    if len(sizes) >= 3:
        med = statistics.median(sizes)
        spread = statistics.pstdev(sizes)
        uniform_tiny = med < THIN_FLOOR and spread < THIN_FLOOR / 2

    flags = []
    if suspect:
        flags.append("thin_html_extract")
    if link_ratio and not link_ratio.get("ok", True):
        flags.append("link_ratio_over_cap")
    if uniform_tiny:
        flags.append("uniform_tiny")
    if len(dups) >= 2 and len(dups) >= len(ok):
        flags.append("mostly_duplicate")
    if not ok and pages:
        flags.append("nothing_extracted")

    return {
        "pages_total": len(pages),
        "pages_ok": len(ok),
        "chars_median": statistics.median(chars) if chars else 0,
        "chars_min": min(chars) if chars else 0,
        "chars_max": max(chars) if chars else 0,
        "via_md_twin": len(md_twin),
        "via_html_fallback": len(html_fallback),
        "thin_pages": [
            {"url": p["url"], "chars": p["chars"], "reason": p.get("reason")} for p in thin
        ],
        "link_ratio": link_ratio,
        "flags": flags,
        "verdict": "SUSPECT" if flags else "OK",
    }
