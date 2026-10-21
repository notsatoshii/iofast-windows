"""Gravitee harvest: pages come from the portal JSON API, never the SPA."""

import json
from types import SimpleNamespace

from iofast_engine.adapters.base import Ctx
from iofast_engine.adapters.gravitee import harvest
from iofast_engine.ledger import Ledger

API = "576af828-be07-40b1-aaf8-28be07e0b1bf"
BASE = f"https://dev.x.io/portal/environments/DEFAULT/apis/{API}"
SPEC = {"openapi": "3.0.1", "info": {"title": "X API"}, "paths": {"/a": {"post": {"tags": ["audit"]}}}}
ROUTES = {
    BASE: {"name": "X API"},
    f"{BASE}/pages?size=-1&homepage=false": {"data": [
        {"id": "p2", "type": "SWAGGER", "order": 2},
        {"id": "p1", "type": "MARKDOWN", "order": 1},
        {"id": "f", "type": "FOLDER", "order": 0},
    ]},
    f"{BASE}/pages/p1?include=content": {"id": "p1", "name": "Getting Started", "type": "MARKDOWN", "content": "Hello"},
    f"{BASE}/pages/p2?include=content": {"id": "p2", "name": "API Reference", "type": "SWAGGER", "content": json.dumps(SPEC)},
}


def get(url):
    body = ROUTES.get(url)
    return SimpleNamespace(status=200 if body else 404, body=json.dumps(body or {}).encode())


def test_harvest_reads_every_doc_page_in_order():
    ledger = Ledger()
    ctx = Ctx(origin="https://dev.x.io", scope=f"https://dev.x.io/catalog/api/{API}/doc?page=p1",
              max_depth=0, get=get, ledger=ledger, emit=lambda e: None)
    docs = list(harvest(ctx))
    urls = [d.url.removeprefix("https://dev.x.io/") for d in docs]
    assert urls == [
        "x-api/getting-started",
        "x-api/api-reference-overview",
        "x-api/api-reference-audit",
        "x-api/api-reference-spec",
    ]
    assert "Hello" in docs[0].markdown and f"doc?page=p1" in docs[0].markdown
    assert '"openapi"' in docs[-1].markdown, "raw spec kept so request bodies are not lost"
    assert not ledger.records, "no fetch failed"


def test_same_title_pages_get_distinct_files_and_bad_json_is_reported():
    routes = dict(ROUTES)
    routes[f"{BASE}/pages?size=-1&homepage=false"] = {"data": [
        {"id": "aaaaaaaa1", "type": "MARKDOWN", "order": 1},
        {"id": "bbbbbbbb2", "type": "MARKDOWN", "order": 2},
        {"id": "cccccccc3", "type": "MARKDOWN", "order": 3},
    ]}
    routes[f"{BASE}/pages/aaaaaaaa1?include=content"] = {"id": "aaaaaaaa1", "name": "Overview", "type": "MARKDOWN", "content": "A"}
    routes[f"{BASE}/pages/bbbbbbbb2?include=content"] = {"id": "bbbbbbbb2", "name": "Overview", "type": "MARKDOWN", "content": "B"}

    def get2(url):
        if url.endswith("cccccccc3?include=content"):
            return SimpleNamespace(status=200, body=b"<html>portal moved</html>")
        body = routes.get(url)
        return SimpleNamespace(status=200 if body else 404, body=json.dumps(body or {}).encode())

    ledger = Ledger()
    ctx = Ctx(origin="https://dev.x.io", scope=f"https://dev.x.io/catalog/api/{API}",
              max_depth=0, get=get2, ledger=ledger, emit=lambda e: None)
    urls = [d.url for d in harvest(ctx)]
    assert len(set(urls)) == 2, urls
    assert [r["reason"] for r in ledger.records.values()] == ["non-JSON 200 (portal API moved?)"]
