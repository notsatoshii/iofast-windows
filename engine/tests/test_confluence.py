"""Confluence harvest. Offline: the REST calls are faked with the committed api-content.json."""

import json
from pathlib import Path

import pytest

from iofast_engine.adapters import run
from iofast_engine.adapters.confluence import ADAPTER, _base_and_space

CONTENT = Path(__file__).parent / "fixtures/platforms/confluence/api-content.json"
START = "https://cwiki.apache.org/confluence/display/KAFKA/Index"
BASE = "https://cwiki.apache.org/confluence"


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status


@pytest.fixture
def calls(monkeypatch):
    """Serve the fixture once, then an empty last page. Records (url, headers) per call."""
    seen = []
    page = json.loads(CONTENT.read_text())

    def fake_get(url, timeout=20, headers=None):
        seen.append((url, headers))
        if len(seen) == 1:
            return FakeResponse(json.dumps(page).encode())
        return FakeResponse(json.dumps({"results": [], "_links": {}}).encode())

    monkeypatch.setattr(run, "api_get", fake_get)
    return seen


def test_base_and_space_from_a_page_url():
    assert _base_and_space(START) == (BASE, "KAFKA")
    assert _base_and_space("https://acme.atlassian.net/wiki/spaces/DOC/pages/7/Home") == (
        "https://acme.atlassian.net/wiki",
        "DOC",
    )


def test_harvest_converts_storage_html_and_records_every_doc(tmp_path, calls):
    ledger, written, _, manifest = run.harvest_run(ADAPTER, START, str(tmp_path))

    assert calls[0][0].startswith(f"{BASE}/rest/api/content?")
    assert "spaceKey=KAFKA" in calls[0][0]
    assert written == 2, "the fixture carries two pages"
    assert manifest["reconciled"]
    assert set(ledger.enqueued) == set(ledger.records)
    assert all(record["outcome"] == "ok" for record in ledger.records.values())
    assert all("?" not in url for url in ledger.enqueued), "no query strings in the ledger"

    text = "\n".join(p.read_text() for p in tmp_path.glob("*.md"))
    assert "# Apache Geronimo Development Process" in text
    assert "<p>" not in text, "storage HTML must be converted, not pasted"
    assert "inner workings of the Geronimo project" in text


def test_token_comes_from_the_environment_never_argv(tmp_path, calls, monkeypatch):
    monkeypatch.setenv("CONFLUENCE_TOKEN", "s3cret")
    run.harvest_run(ADAPTER, START, str(tmp_path))

    assert calls[0][1] == {"Authorization": "Bearer s3cret"}


def test_anonymous_when_no_token_is_set(tmp_path, calls, monkeypatch):
    monkeypatch.delenv("CONFLUENCE_TOKEN", raising=False)
    run.harvest_run(ADAPTER, START, str(tmp_path))

    assert calls[0][1] is None


def test_failed_list_call_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "api_get", lambda url, timeout=20, headers=None: FakeResponse(b"", 401))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, START, str(tmp_path))

    assert written == 0
    assert manifest["reconciled"]
    failure = ledger.records[f"{BASE}/rest/api/content"]
    assert (failure["outcome"], failure["status"]) == ("failed_fetch", 401)
