"""Ported from doc_to_md.py selftest(): scope, depth, and the link filter."""

from iofast_engine.urls import (
    bad_href,
    clean_url,
    is_asset,
    md_twins,
    md_variant,
    norm_host,
    path_depth,
    reject_url,
    scope_prefix,
)

HOST, SCOPE, DEPTH = "docs.x.com", "/docs/", 2
KEEP = frozenset()


def test_scope_prefix():
    assert scope_prefix("https://x.com/docs/intro") == "/docs/"
    assert scope_prefix("https://x.com/3/library/") == "/3/library/"
    assert scope_prefix("https://x.com") == "/"


def test_path_depth():
    assert path_depth("https://x.com/docs/api/v2/auth", "/docs/") == 3
    assert path_depth("https://x.com/docs/", "/docs/") == 0
    assert path_depth("https://x.com/blog/post", "/docs/") == -1, "out-of-scope must be -1"


def test_reject_url_accepts_in_scope():
    assert reject_url("https://docs.x.com/docs/guide", HOST, SCOPE, DEPTH, KEEP) is None
    assert reject_url("https://www.docs.x.com/docs/guide", HOST, SCOPE, DEPTH, KEEP) is None
    assert reject_url("https://docs.x.com/docs/fr/a", HOST, SCOPE, DEPTH, frozenset({"fr"})) is None


def test_reject_url_every_reason():
    assert reject_url("https://evil.com/docs/guide", HOST, SCOPE, DEPTH, KEEP) == "offsite"
    assert reject_url("https://docs.x.com/docs/a.png", HOST, SCOPE, DEPTH, KEEP) == "asset"
    assert reject_url("https://docs.x.com/pricing", HOST, SCOPE, DEPTH, KEEP) == "out_of_scope"
    assert reject_url("https://docs.x.com/docs/a/b/c", HOST, SCOPE, DEPTH, KEEP) == "too_deep"
    assert reject_url("https://docs.x.com/docs/fr/a", HOST, SCOPE, DEPTH, KEEP) == "locale_dupe"
    assert (
        reject_url("https://docs.x.com/docs/changelog", HOST, SCOPE, DEPTH, KEEP)
        == "excluded_pattern"
    )


def test_href_and_asset_helpers():
    assert bad_href("mailto:a@b.c") and bad_href("#top") and not bad_href("/docs/x")
    assert is_asset("https://x/a/s.zip") and not is_asset("https://x/a/guide.html")


def test_md_variant():
    # Mintlify / llms.txt convention: the markdown twin is `<path>.md`, which is
    # exactly what an llms.txt link list already points at. The older
    # `<path>/index.html.md` form 404s on these sites.
    assert md_variant("https://x/docs/a") == "https://x/docs/a.md"
    assert md_variant("https://x/docs/a/") == "https://x/docs/a.md"
    assert md_variant("https://x/docs/a.html") == "https://x/docs/a.html.md"
    # Idempotent: an already-.md url (llms.txt hands these directly) is unchanged.
    assert md_variant("https://x/docs/a.md") == "https://x/docs/a.md"
    # Site root has no `<path>.md` twin — leave it for the HTML fallback.
    assert md_variant("https://x/") == "https://x/"


def test_md_twins_only_counts_per_page_md_links():
    # Mintlify llms.txt: per-page `.md` twins -> canonical page urls, md-capable.
    assert md_twins(["https://x/docs/a.md", "https://x/docs/b.md"]) == [
        "https://x/docs/a",
        "https://x/docs/b",
    ]
    # Generic bundle-only llms.txt (svelte-shaped): NO per-page `.md` -> empty, so
    # the crawl stays md-incapable and never double-fetches a 404ing twin.
    assert md_twins(["https://x/llms-full.txt", "https://x/llms-small.txt"]) == []
    assert md_twins([]) == []


def test_md_twins_canonicalise_index_convention():
    # Cloudflare convention (measured 2026-08-13): twins live at `<path>/index.md`
    # while the sitemap lists `<path>` — canonicalising must strip `/index` or the
    # same page enters the crawl twice under two urls.
    assert md_twins(["https://x/docs/get-started/index.md"]) == ["https://x/docs/get-started"]
    assert md_twins(["https://x/index.md"]) == ["https://x"]


def test_md_twin_map_keeps_exact_twin_urls():
    from iofast_engine.urls import md_twin_map

    # The llms.txt link IS the twin url — re-deriving it with md_variant assumes
    # the `<path>.md` convention and 404s on `<path>/index.md` sites.
    twins = [
        "https://x/docs/a.md",
        "https://x/docs/get-started/index.md",
    ]
    assert md_twin_map(twins) == {
        "https://x/docs/a": "https://x/docs/a.md",
        "https://x/docs/get-started": "https://x/docs/get-started/index.md",
    }
    assert md_twin_map(["https://x/llms-full.txt"]) == {}


def test_clean_url_strips_query_and_fragment():
    assert clean_url("https://x.com/a/b?q=1&r=2#top") == "https://x.com/a/b"
    assert norm_host("WWW.Example.com") == "example.com"


def test_unparseable_url_is_excluded_not_raised():
    """llms.txt is untrusted input — a bracketed placeholder host must not kill the run."""
    placeholder = "https://[organization-name].gitbook.io/[site-title]"
    assert reject_url(placeholder, HOST, SCOPE, DEPTH, KEEP) == "unparseable"
