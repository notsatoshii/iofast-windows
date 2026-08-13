"""Pin the quality harness's analyse() — its flags drove the 2026-08-13 audit
and back the committed discovery constants, so the logic must not drift."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.scrape_quality import HTML_REASONS, THIN_FLOOR, analyse


def _manifest(pages, link_ratio_ok=True):
    return {
        "pages": pages,
        "clean": {"link_ratio": {"value": 0.1, "cap": 0.5, "ok": link_ratio_ok}},
    }


def _page(chars, reason="selector+trafilatura", outcome="ok", url="https://x/p"):
    return {"url": url, "chars": chars, "reason": reason, "outcome": outcome}


def test_healthy_manifest_is_ok():
    report = analyse(_manifest([_page(5000), _page(3000), _page(4000)]))
    assert report["verdict"] == "OK"
    assert report["flags"] == []


def test_thin_html_extract_flags_suspect():
    report = analyse(_manifest([_page(120), _page(150), _page(90)]))
    assert "thin_html_extract" in report["flags"]
    assert "uniform_tiny" in report["flags"]
    assert report["verdict"] == "SUSPECT"
    assert len(report["thin_pages"]) == 3


def test_md_twin_pages_never_flag_thin_html():
    # A twin-sourced page is real markdown, not a mangled HTML fragment.
    report = analyse(_manifest([_page(200, reason="llms.txt .md variant")]))
    assert "thin_html_extract" not in report["flags"]
    assert report["via_md_twin"] == 1


def test_escalated_pages_count_as_extracted():
    report = analyse(_manifest([_page(1445, outcome="thin_escalated_ok")]))
    assert report["pages_ok"] == 1
    assert report["chars_median"] == 1445


def test_link_ratio_over_cap_flags():
    report = analyse(_manifest([_page(5000)], link_ratio_ok=False))
    assert "link_ratio_over_cap" in report["flags"]


def test_nothing_extracted_flags():
    report = analyse(_manifest([_page(0, outcome="failed_fetch")]))
    assert "nothing_extracted" in report["flags"]


def test_constants_are_sane():
    assert THIN_FLOOR == 800
    assert "selector+markdownify" in HTML_REASONS
