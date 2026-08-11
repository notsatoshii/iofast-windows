"""MUST-FAIL fixture for the harvest ledger contract.

An api run must not fake a green report. This adapter yields three docs and then
raises on the fourth URL it enqueued: the report has to show 3 ok + 1
failed_exception AND still reconcile — green *because the failure is recorded*,
not because nothing was ever enqueued.

Discipline check (2026-08-11): replacing the `for url in ledger.enqueued -
set(ledger.records): ledger.record(...)` loop in adapters/run.py with `pass` made
this test fail:

    >       assert ledger.records[BOOM]["outcome"] == "failed_exception"
    E       AssertionError: assert 'failed_fetch' == 'failed_exception'

i.e. the raise got laundered into reconcile()'s generic "dropped after retries"
row. The assertions are load-bearing, not decorative.
"""

from iofast_engine.adapters.base import Adapter, Doc
from iofast_engine.adapters.run import harvest_run
from iofast_engine.cli import exit_code

DOCS = [f"https://api.example.com/docs/{n}" for n in range(3)]
BOOM = "https://api.example.com/docs/3"


def harvest(ctx):
    for url in DOCS:
        ctx.ledger.enqueue(url)
        yield Doc(url=url, markdown=f"# {url}\n\nbody\n")
    ctx.ledger.enqueue(BOOM)  # enqueued, then the fetch blows up
    raise RuntimeError("upstream 500 on doc 4")


FLAKY = Adapter(name="flaky", detect=lambda fp: False, fetch_mode="api", harvest=harvest)


def test_a_raising_harvest_reports_three_ok_and_one_failure(tmp_path):
    ledger, written, out_dir, manifest = harvest_run(FLAKY, "https://api.example.com/x", str(tmp_path))
    report = (tmp_path / "report.md").read_text()

    assert written == 3
    assert len(ledger.enqueued) == 4, "the failed URL stays enqueued — no silent un-enqueue"
    assert ledger.records[BOOM]["outcome"] == "failed_exception"
    assert "RuntimeError: upstream 500 on doc 4" in ledger.records[BOOM]["reason"]

    assert "| ok | 3 |" in report
    assert "| failed_exception | 1 |" in report
    assert "Ledger reconciles: 4 recorded of 4 enqueued (OK — zero silent failures)." in report
    assert BOOM in report, "the failure is named in the report, not just counted"
    assert manifest["reconciled"] is True
    assert exit_code(ledger, written) == 1, "a failed doc must not exit 0"
