"""SwaggerHub harvest. Offline: the one HTTP call is faked with the committed spec.json."""

import json
from pathlib import Path

import pytest

from iofast_engine.adapters import run
from iofast_engine.adapters.swaggerhub import ADAPTER

SPEC = Path(__file__).parent / "fixtures/platforms/swaggerhub/spec.json"
START = "https://app.swaggerhub.com/apis-docs/swagger-hub/registry-api/1.3.0"
SPEC_URL = "https://api.swaggerhub.com/apis/swagger-hub/registry-api/1.3.0"


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status


@pytest.fixture
def fetched(monkeypatch):
    """Record every URL the adapter asks for; serve the spec, nothing else."""
    calls = []

    def fake_get(url, timeout=20, headers=None):
        calls.append(url)
        if url == SPEC_URL:
            return FakeResponse(SPEC.read_bytes())
        return FakeResponse(b"", status=404)

    monkeypatch.setattr(run, "api_get", fake_get)
    return calls


def test_harvest_renders_docs_from_the_spec(tmp_path, fetched):
    ledger, written, out_dir, manifest = run.harvest_run(ADAPTER, START, str(tmp_path))

    assert fetched == [SPEC_URL], "the SPA must never be fetched"
    assert written >= 2, "an overview plus at least one tag doc"
    assert manifest["reconciled"]
    assert manifest["info"]["discovery"] == "api (swaggerhub)"
    assert set(ledger.enqueued) == set(ledger.records), "every enqueued doc is recorded"
    assert all(r["outcome"] == "ok" for r in ledger.records.values())
    assert all(url.startswith(SPEC_URL + "/") for url in ledger.enqueued)


def test_markdown_carries_operation_headings(tmp_path, fetched):
    run.harvest_run(ADAPTER, START, str(tmp_path))
    text = "\n".join(p.read_text() for p in tmp_path.glob("*.md"))

    assert "# SwaggerHub Registry API" in text
    assert "## GET /specs" in text
    assert "| Name | In | Required | Type |" in text
    assert "| Code | Description | Schema |" in text
    # Schema NAMES, never a dump of the schema itself.
    spec = json.loads(SPEC.read_text())
    assert '"properties"' not in text
    assert any(name in text for name in spec["definitions"])


def test_failed_spec_fetch_is_recorded_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "api_get", lambda url, timeout=20, headers=None: FakeResponse(b"", 503))
    ledger, written, _, manifest = run.harvest_run(ADAPTER, START, str(tmp_path))

    assert written == 0
    assert manifest["reconciled"]
    assert ledger.records[SPEC_URL]["outcome"] == "failed_fetch"
    assert ledger.records[SPEC_URL]["status"] == 503
