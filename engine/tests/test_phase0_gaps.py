"""Phase 0 known gaps, closed: suffix twins, exhausted-retry status, scope root, redirect scope."""

import asyncio

import iofast_engine.discovery as discovery
from iofast_engine.urls import path_depth, reject_url, suffix_twin

from test_rescue import _stub_spider


# --- gap 1: -cn locale-suffix twins (ant.design run b had 20 Chinese duplicates) ---


def test_suffix_twin_dropped_only_when_base_exists():
    urls = {"https://x.com/docs/react/button", "https://x.com/docs/react/button-cn"}
    assert suffix_twin("https://x.com/docs/react/button-cn", urls)
    assert not suffix_twin("https://x.com/docs/react/button", urls)
    # No un-suffixed sibling -> a page genuinely named foo-cn survives (must-fail case).
    assert not suffix_twin("https://x.com/docs/react/lone-cn", urls)
    # Language-code suffixes twin the same way; keep_locales exempts them.
    urls_de = {"https://x.com/a/setup", "https://x.com/a/setup-de"}
    assert suffix_twin("https://x.com/a/setup-de", urls_de)
    assert not suffix_twin("https://x.com/a/setup-de", urls_de, frozenset({"de"}))


def test_reconcile_drops_suffix_twins(tmp_path):
    s = _stub_spider(tmp_path)
    kept = s._reconcile(
        [
            "https://x.com/docs/button",
            "https://x.com/docs/button-cn",
            "https://x.com/docs/lone-cn",
        ]
    )
    assert kept == ["https://x.com/docs/button", "https://x.com/docs/lone-cn"]
    assert s.ledger.excluded["locale_suffix_twin"] == 1


# --- gap 2: exhausted 429/503 keep their status code ---


def test_exhausted_transient_code_reports_real_status(tmp_path):
    s = _stub_spider(tmp_path)
    s.start_url, s.discovery, s.seeds = "https://x.com/docs", "sitemap", []

    class Blocked:
        url = "https://x.com/docs/rate-limited"
        status = 429
        meta = {"origin_url": "https://x.com/docs/rate-limited"}

    s.ledger.enqueue(Blocked.url)
    assert asyncio.run(s.is_blocked(Blocked())) is True
    # Upstream drops the request after retries with no callback; on_close fills in.
    asyncio.run(s.on_close())
    record = s.ledger.records[Blocked.url]
    assert record["outcome"] == "failed_fetch"
    assert record["status"] == 429
    assert "retries exhausted" in record["reason"]
    assert "no response recorded" not in record["reason"]


# --- gap 3: the scope-root page is in scope ---


def test_scope_root_page_is_depth_zero():
    assert path_depth("https://x.com/docs", "/docs/") == 0
    assert path_depth("https://x.com", "/") == 0
    assert reject_url("https://x.com/docs", "x.com", "/docs/", 3) is None
    assert reject_url("https://x.com/blog", "x.com", "/docs/", 3) == "out_of_scope"


# --- gap 4: cross-host redirect to a deeper path adopts that path ---


def _fake_get(monkeypatch, final_url):
    class Response:
        url = final_url

    monkeypatch.setattr(discovery, "_get", lambda url, timeout=20: Response())


def test_root_start_adopts_redirect_path(monkeypatch):
    _fake_get(monkeypatch, "https://gitbook.com/docs/")
    assert discovery.resolve_start("https://docs.gitbook.com") == "https://gitbook.com/docs/"


def test_path_rewriting_redirect_is_trusted(monkeypatch):
    # docs.readthedocs.io/en/stable/ -> docs.readthedocs.com/platform/en/stable/:
    # the server's mapping resolves; the old path on the new host 404s.
    _fake_get(monkeypatch, "https://docs.readthedocs.com/platform/en/stable/")
    got = discovery.resolve_start("https://docs.readthedocs.io/en/stable/")
    assert got == "https://docs.readthedocs.com/platform/en/stable/"


def test_redirect_to_root_restores_user_path(monkeypatch):
    _fake_get(monkeypatch, "https://new.example.com/")
    got = discovery.resolve_start("https://old.example.com/docs/intro")
    assert got == "https://new.example.com/docs/intro", "root landing loses the user's scope"


def test_same_host_redirect_unchanged(monkeypatch):
    _fake_get(monkeypatch, "https://x.com/docs/intro")
    assert discovery.resolve_start("https://x.com/docs") == "https://x.com/docs"
