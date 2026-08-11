"""known_fail: the engine refuses, writes the reason, scrapes nothing, exits 1."""

import json
from pathlib import Path

from iofast_engine.adapters.detection import detect_late
from iofast_engine.adapters.notion import ADAPTER
from iofast_engine.adapters.run import known_fail_run
from iofast_engine.cli import exit_code

FIXTURE = Path(__file__).parent / "fixtures/platforms/notion"
URL = "https://theiagen.notion.site/Theiagen-Public-Health-Resources-a4bd134b0c5c4fe39870e21029a30566"


def test_notion_fixture_detects_as_a_known_fail():
    adapter = detect_late(URL, 200, {}, (FIXTURE / "static-theiagen.html").read_text())
    assert adapter is ADAPTER
    assert "Export the page from Notion instead." in adapter.known_fail


def test_engine_refuses_and_the_ledger_stays_honest(tmp_path):
    ledger, written, out_dir, manifest = known_fail_run(ADAPTER, URL, str(tmp_path))
    report = (tmp_path / "report.md").read_text()

    assert written == 0
    assert exit_code(ledger, written) == 1
    assert [p.name for p in sorted(tmp_path.iterdir())] == [
        "README-UNTRUSTED.md",
        "manifest.json",
        "report.md",
    ]

    assert ADAPTER.known_fail in report
    assert "| failed_fetch | 1 |" in report
    assert URL in report
    assert ledger.records[URL]["outcome"] == "failed_fetch"
    assert manifest["reconciled"] is True
    assert manifest["info"]["platform"] == "notion"
    assert json.loads((tmp_path / "manifest.json").read_text())["info"]["known fail"]
