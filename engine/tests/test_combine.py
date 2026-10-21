"""`iofast combine`: one LLM-ready file per site, in site order, boilerplate gone."""

import json

from iofast_engine.cli import main
from iofast_engine.combine import combine
from iofast_engine.ledger import Ledger
from iofast_engine.manifest import build_manifest
from iofast_engine.writer import envelope

FOOTER = "Was this page helpful? Tell us on [Discord](https://x.test/discord)."
AGENT = "## Ask this page\nGET the page with `?ask=` to query it."


def _section(site, name, pages, start="https://x.test/"):
    """pages: [(url, file, body)] in site order -> a section folder with a manifest."""
    folder = site / name
    folder.mkdir(parents=True)
    rows = []
    for url, file, body in pages:
        (folder / file).write_text(envelope(url, body), encoding="utf-8")
        rows.append({"url": url, "outcome": "ok", "file": file})
    (folder / "manifest.json").write_text(
        json.dumps({"info": {"start url": start}, "pages": rows}), encoding="utf-8"
    )
    (folder / "report.md").write_text("# Scrape report\n", encoding="utf-8")
    return folder


def _page(title, text, url):
    # A per-page footer naming the page's own URL is still one repeated block.
    return f"# {title}\n\n{text}\n\n{AGENT}\n\nSee [this page]({url}).\n\n{FOOTER}\n"


def test_manifest_keeps_site_order_not_url_order():
    ledger = Ledger()
    for url in ("https://x.test/zeta", "https://x.test/alpha"):
        ledger.enqueue(url)
    ledger.record("https://x.test/alpha", "ok", file="alpha.md")
    ledger.record("https://x.test/zeta", "ok", file="zeta.md")
    pages = build_manifest({}, ledger, True)["pages"]
    assert [p["url"] for p in pages] == ["https://x.test/zeta", "https://x.test/alpha"]


def test_combine_orders_dedupes_and_lands_in_site_folder(tmp_path):
    site = tmp_path / "docs-x-test"
    urls = [f"https://x.test/{n}" for n in ("zeta", "alpha", "mid")]
    _section(
        site,
        "_root",
        [
            (urls[0], "zeta.md", _page("Zeta intro", "Zeta text.\n\n## Deep\n\nShared note.", urls[0])),
            (urls[1], "alpha.md", _page("Alpha", "Alpha text.\n\nShared note.", urls[1])),
            (urls[2], "mid.md", _page("Mid", "Mid text.\n\n```sh\n# not a heading\n```", urls[2])),
        ],
    )
    (site / "_stale" / "_root").mkdir(parents=True)
    (site / "_stale" / "_root" / "manifest.json").write_text('{"pages": []}')

    target = combine(site)

    assert target == site.resolve() / "docs-x-test.md"
    text = target.read_text(encoding="utf-8")
    # Site order from the manifest, not alphabetical.
    assert text.index("## Zeta intro") < text.index("## Alpha") < text.index("## Mid")
    # Envelope once, condensed; no per-file SOURCE lines.
    assert text.count("UNTRUSTED CONTENT") == 1 and "--- SOURCE:" not in text
    assert "Source: https://x.test/alpha" in text
    # Blocks on every page are gone; a block on only 2 pages is real content.
    assert "helpful" not in text and "Ask this page" not in text and "See [this page]" not in text
    assert text.count("Shared note.") == 2
    # Page headings sit under the page heading; code fences are untouched.
    assert "### Deep" in text and "# not a heading" in text
    assert "## Contents\n\n1. Zeta intro\n2. Alpha\n3. Mid" in text


def test_combine_puts_each_section_in_its_own_part(tmp_path):
    site = tmp_path / "docs-x-test"
    _section(site, "api-b", [("https://x.test/b/one", "one.md", "# One\n\nB text.\n")])
    _section(site, "_root", [("https://x.test/home", "home.md", "# Home\n\nRoot text.\n")])

    assert main(["combine", str(site)]) == 0

    text = (site / "docs-x-test.md").read_text(encoding="utf-8")
    assert text.index("## Part 1: home") < text.index("### Home") < text.index("## Part 2: b")
    assert "### One" in text
    assert not (site / "_root" / "docs-x-test.md").exists()


def test_combine_with_no_pages_fails(tmp_path, capsys):
    assert main(["combine", str(tmp_path)]) == 1
    assert "no scraped pages" in capsys.readouterr().err


def test_combine_keeps_real_content_that_looks_repeated(tmp_path):
    """Review cases: curl samples that differ by URL, shared section headings,
    a 4-backtick fence holding a 3-backtick one, a heading ending in #."""
    site = tmp_path / "docs-x-test"
    pages = []
    for n in range(4):
        url = f"https://x.test/ep{n}"
        body = (
            f"# Endpoint {n}\n\n## Parameters\n\nParam text {n}.\n\n"
            f"```sh\ncurl https://api.x.test/v1/r{n}\n```\n\n"
            f"````md\n```js\nx{n}\n```\n\nEnd of example.\n````\n\n## Using C#\n\n"
            f"Last updated entries come back from API {'abcd'[n]}.\n\n{FOOTER}\n"
        )
        pages.append((url, f"ep{n}.md", body))
    _section(site, "_root", pages)

    text = combine(site).read_text(encoding="utf-8")

    assert text.count("### Parameters") == 4
    assert all(f"curl https://api.x.test/v1/r{n}" in text for n in range(4))
    assert text.count("End of example.") == 4 and "### Using C#" in text
    assert text.count("Last updated entries") == 4 and "helpful" not in text
    assert not list(site.glob("*.tmp"))
