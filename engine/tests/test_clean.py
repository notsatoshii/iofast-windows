"""The numeric clean definition — each check proven with an input that MUST fail it."""

from iofast_engine.clean import LINK_RATIO_CAP, MIN_CHARS, evaluate
from iofast_engine.ledger import Ledger
from iofast_engine.writer import Writer

CONTENT = (
    "# Guide\n\nThis page explains the setup in plain prose with enough real text "
    "to clear every measured floor. " * 5
)


def run(tmp_path, pages, fail_urls=()):
    ledger = Ledger()
    writer = Writer(str(tmp_path), ledger, max_pages=len(pages) + len(fail_urls))
    for url, text in pages:
        writer.write(url, text)
    for url in fail_urls:
        ledger.enqueue(url)
        ledger.record(url, "failed_fetch", "http 503")
    return evaluate(ledger, str(tmp_path))


def test_healthy_run_is_clean(tmp_path):
    verdict = run(tmp_path, [(f"https://x.com/docs/p{i}", CONTENT) for i in range(20)])
    assert verdict["clean"] is True
    assert verdict["coverage"]["value"] == 1.0


def test_coverage_below_floor_fails(tmp_path):
    # 18 written of 20 enqueued = 90% < 95%.
    verdict = run(
        tmp_path,
        [(f"https://x.com/docs/p{i}", CONTENT) for i in range(18)],
        fail_urls=["https://x.com/docs/f1", "https://x.com/docs/f2"],
    )
    assert verdict["coverage"]["ok"] is False
    assert verdict["clean"] is False


def test_file_under_char_floor_fails(tmp_path):
    verdict = run(
        tmp_path,
        [("https://x.com/docs/good", CONTENT), ("https://x.com/docs/junk", "x" * (MIN_CHARS - 1))],
    )
    assert verdict["min_chars"]["ok"] is False
    assert verdict["min_chars"]["files_under"] == ["docs-junk.md"]
    assert verdict["clean"] is False


def test_nav_shell_corpus_fails_link_ratio(tmp_path):
    shell = "\n".join(f"- [Section {i} of the sidebar](https://x.com/docs/s{i})" for i in range(30))
    verdict = run(tmp_path, [(f"https://x.com/docs/p{i}", shell) for i in range(5)])
    assert verdict["link_ratio"]["value"] > LINK_RATIO_CAP
    assert verdict["link_ratio"]["ok"] is False
    assert verdict["clean"] is False


def test_one_index_page_does_not_fail_a_healthy_corpus(tmp_path):
    """The cap is corpus-wide by design: a genuine TOC page (measured up to 0.88
    link ratio on the readme/redocly landing fixtures) must not fail a real run."""
    toc = "\n".join(f"- [Chapter {i}](https://x.com/docs/c{i})" for i in range(20))
    pages = [(f"https://x.com/docs/p{i}", CONTENT) for i in range(10)]
    pages.append(("https://x.com/docs/index-toc", toc))
    verdict = run(tmp_path, pages)
    assert verdict["clean"] is True
