"""Ported from doc_to_md.py selftest(): ledger classification + reconciliation."""

import pytest

from iofast_engine.ledger import Ledger, report_md
from iofast_engine.manifest import build_manifest


def build():
    ledger = Ledger()
    for url in ("u/ok", "u/thin", "u/skip", "u/boom", "u/lost"):
        ledger.enqueue(url)
    ledger.record("u/ok", "ok", "selector+trafilatura", chars=900, file="ok.md")
    ledger.record("u/thin", "thin_escalated_ok", "trafilatura", tier="dynamic", chars=800)
    ledger.record("u/skip", "skipped_no_content", "no extractable content")
    ledger.record("u/boom", "failed_exception", "TimeoutError")
    ledger.exclude("asset")
    return ledger


def test_counts_and_reconcile():
    ledger = build()
    assert ledger.counts()["ok"] == 1 and len(ledger.records) == 4
    assert ledger.reconcile(), "reconcile must close the gap"
    assert ledger.records["u/lost"]["outcome"] == "failed_fetch", "unrecorded url must fail loudly"
    assert sum(ledger.counts().values()) == len(ledger.enqueued) == 5


def test_unknown_outcome_raises():
    ledger = build()
    with pytest.raises(ValueError):
        ledger.record("u/ok", "definitely_fine")


def test_report_reconcile_line():
    ledger = build()
    ledger.reconcile()  # report_md totals the records it is given, as it always has
    assert "Ledger reconciles: 5 recorded of 5 enqueued" in report_md(ledger, {"start url": "x"})


def test_reconcile_synthetic_rows_keep_status_none():
    ledger = build()
    ledger.reconcile()
    assert ledger.records["u/lost"]["status"] is None
    assert ledger.records["u/lost"]["elapsed_ms"] is None


def test_outcome_is_not_inferred_from_tier():
    """A first-try success at a non-static tier is still 'ok'."""
    ledger = Ledger()
    ledger.record("u/a", "ok", tier="stealth", escalated=False)
    assert ledger.records["u/a"]["outcome"] == "ok"
    assert ledger.records["u/a"]["tier"] == "stealth"
    assert ledger.records["u/a"]["escalated"] is False


def test_manifest_carries_new_fields():
    ledger = Ledger()
    ledger.record(
        "u/a", "thin_escalated_ok", "trafilatura", tier="dynamic",
        chars=800, file="a.md", status=200, escalated=True, elapsed_ms=1234,
    )
    manifest = build_manifest({"start url": "x"}, ledger, ledger.reconcile())
    assert manifest["schema"] == 1
    row = manifest["pages"][0]
    assert row["status"] == 200
    assert row["escalated"] is True
    assert row["elapsed_ms"] == 1234
    assert row["platform"] is None
    assert set(row) == {
        "url", "outcome", "reason", "tier", "escalated",
        "status", "chars", "file", "elapsed_ms", "platform",
        "etag", "last_modified",  # additive (schema 1): re-scrape validators
    }
