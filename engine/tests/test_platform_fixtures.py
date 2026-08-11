"""The committed captures are the offline substitute for the live web — keep them honest.

Refresh them with `engine/tests/fixtures/capture.py`; these tests fail the moment a
fixture, its meta row, or the recorded extraction size drift apart.
"""

import json
from pathlib import Path

import pytest

from iofast_engine.extract import extract_markdown
from iofast_engine.tiers import is_challenge

FIXTURES = Path(__file__).parent / "fixtures"
METAS = sorted(FIXTURES.glob("platforms/*/meta.json")) + [FIXTURES / "negatives" / "meta.json"]
SIZE_CAP = 2_000_000
ROWS = [(meta, row) for meta in METAS for row in json.loads(meta.read_text())["pages"]]

REQUIRED = {"file", "url", "captured", "status", "headers", "variant", "extracted_chars", "synthetic"}
EXPECTED_PLATFORMS = {
    "gitbook", "mintlify", "docusaurus", "readme", "redocly",
    "document360", "readthedocs", "notion", "swaggerhub", "confluence",
}


def ident(pair):
    meta, row = pair
    return f"{meta.parent.name}/{row['file']}"


def test_every_platform_is_captured():
    assert {m.parent.name for m in METAS} == EXPECTED_PLATFORMS | {"negatives"}


@pytest.mark.parametrize("pair", ROWS, ids=[ident(p) for p in ROWS])
def test_row_matches_the_file_on_disk(pair):
    meta, row = pair
    assert REQUIRED <= set(row), f"missing keys: {REQUIRED - set(row)}"
    path = meta.parent / row["file"]
    assert path.exists(), "meta.json names a file that is not committed"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), "empty fixture"
    assert len(text.encode()) < SIZE_CAP, "fixture over the 2MB commit cap"
    assert row["variant"] in ("static", "rendered", "api")
    assert all(k == k.lower() for k in row["headers"]), "headers must be lowercased for detect()"
    assert len(extract_markdown(text, row["url"])) == row["extracted_chars"], (
        "extracted_chars is stale — re-run capture.py"
    )


@pytest.mark.parametrize("meta", METAS, ids=[m.parent.name for m in METAS])
def test_no_orphan_fixture_files(meta):
    """A file nobody's meta row describes is invisible to every adapter test."""
    named = {row["file"] for row in json.loads(meta.read_text())["pages"]}
    on_disk = {p.name for p in meta.parent.iterdir() if p.name != "meta.json"}
    assert on_disk == named


def test_challenge_detection_on_committed_bodies():
    """is_challenge() must fire on the challenge fixture and on nothing else."""
    challenge = next(
        row for row in json.loads((FIXTURES / "negatives" / "meta.json").read_text())["pages"]
        if row["file"] == "cloudflare-challenge.html"
    )
    body = (FIXTURES / "negatives" / "cloudflare-challenge.html").read_text(encoding="utf-8")
    assert is_challenge(challenge["status"], challenge["headers"], body)

    for meta, row in ROWS:
        if row["file"] == "cloudflare-challenge.html":
            continue
        text = (meta.parent / row["file"]).read_text(encoding="utf-8")
        assert not is_challenge(row["status"], row["headers"], text), ident((meta, row))


def test_notion_static_html_is_a_shell_but_the_rendered_dom_is_not():
    """The fixture pair that justifies the dynamic tier existing at all."""
    rows = {r["file"]: r for r in json.loads((FIXTURES / "platforms/notion/meta.json").read_text())["pages"]}
    static = FIXTURES / "platforms/notion/static-theiagen.html"
    rendered = FIXTURES / "platforms/notion/rendered-theiagen.html"
    assert len(static.read_bytes()) * 3 < len(rendered.read_bytes())
    assert rows["rendered-theiagen.html"]["extracted_chars"] > rows["static-theiagen.html"]["extracted_chars"]
