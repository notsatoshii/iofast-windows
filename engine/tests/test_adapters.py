"""Registry rules + the fingerprint cross-product.

Every committed static fixture page must match EXACTLY its own adapter, and the three
negatives must match none. A marker that leaks across platforms fails here.
"""

import json
from pathlib import Path

import pytest

from iofast_engine.adapters import ADAPTERS, detect_platform
from iofast_engine.adapters.base import Adapter, Fingerprint
from iofast_engine.adapters.detection import fingerprint

FIXTURES = Path(__file__).parent / "fixtures"
FP = Fingerprint(url="https://x.com/docs/a", status=200, headers={}, html="<html></html>")


def rows():
    """(expected platform or None, Fingerprint) for every committed HTML page."""
    metas = sorted(FIXTURES.glob("platforms/*/meta.json")) + [FIXTURES / "negatives/meta.json"]
    for meta in metas:
        data = json.loads(meta.read_text())
        platform = data.get("platform")
        if platform == "negatives":  # plain / 404 / cloudflare challenge: must match nothing
            platform = None
        for row in data["pages"]:
            if not row["file"].endswith(".html"):
                continue
            html = (meta.parent / row["file"]).read_text(encoding="utf-8")
            yield (
                platform,
                row["file"],
                fingerprint(row["url"], row["status"], row["headers"], html),
            )


CASES = list(rows())


def test_registry_entries_are_plain_adapters():
    """Subclassing an Adapter is forbidden: detection must stay data, not dispatch."""
    for adapter in ADAPTERS:
        assert type(adapter) is Adapter, f"{adapter} must be an Adapter, not a subclass"


def test_registry_shape():
    assert len(ADAPTERS) == 11
    names = [a.name for a in ADAPTERS]
    assert len(set(names)) == len(names), f"duplicate adapter names: {names}"


def test_the_cross_product_covers_every_platform():
    assert {platform for platform, _, _ in CASES if platform} == {a.name for a in ADAPTERS}


@pytest.mark.parametrize(
    ("expected", "name", "fp"), CASES, ids=[f"{p or 'negative'}/{n}" for p, n, _ in CASES]
)
def test_exactly_one_adapter_matches_each_fixture(expected, name, fp):
    matched = [a.name for a in ADAPTERS if a.detect(fp)]
    assert matched == ([expected] if expected else []), (
        f"{name}: expected {expected or 'no match'}, got {matched}"
    )


def test_detect_platform_first_match_and_ambiguity_event():
    events = []
    always = Adapter(name="always", detect=lambda fp: True)
    also = Adapter(name="also", detect=lambda fp: True)
    never = Adapter(name="never", detect=lambda fp: False)

    import iofast_engine.adapters as registry

    original = registry.ADAPTERS
    try:
        registry.ADAPTERS = (always, never, also)
        assert detect_platform(FP, emit=events.append) is always
    finally:
        registry.ADAPTERS = original

    assert [e["warning"] for e in events] == ["ambiguous_platform"]


def test_settings_the_fixtures_justify():
    """The two shells get the dynamic tier; the SSR'd platforms stay static."""
    settings = {a.name: a for a in ADAPTERS}
    assert settings["document360"].fetch_strategy == "dynamic"
    assert settings["readme"].fetch_strategy == "dynamic"
    assert settings["gitbook"].fetch_strategy == "static"
    assert settings["mintlify"].fetch_strategy == "static"
    assert settings["swaggerhub"].fetch_mode == "api"
    assert settings["confluence"].fetch_mode == "api"
    # The docsify shell extracts 0 chars (static-landing.html) — the .md files are the run.
    assert settings["docsify"].fetch_mode == "api"
    assert settings["notion"].known_fail and settings["notion"].harvest is None
    # nav_selector is stored for step 4; nothing wires it yet.
    assert settings["docusaurus"].nav_selector == 'nav[aria-label="Docs sidebar"]'
