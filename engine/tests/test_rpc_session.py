"""RPC sidecar contract tests — offline, deterministic. See docs/RPC.md.

dispatch() is driven directly (not through the stdin loop) so joining the run
thread between steps makes output ordering deterministic; the stdin loop and
EOF path get their own test. The recorded session fixture is asserted
byte-for-byte against the server's real serializer output.

The must-reject cases come first: a guard is only trusted once the input that
MUST fail actually fails.
"""

import asyncio
import io
import json
import os
import threading

import pytest

import iofast_engine.rpc as rpc
from iofast_engine import __version__
from iofast_engine.ledger import FAILURE_OUTCOMES, OUTCOMES, Ledger
from iofast_engine.rpc import RpcServer
from iofast_engine.runner import exit_code

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rpc", "session.json")

URL = "https://docs.example.com/docs/"


def make_server(stdin=None, exit_fn=None):
    out = io.StringIO()
    server = RpcServer(stdin=stdin, stdout=out, exit_fn=exit_fn or (lambda code: None))
    return server, out


def lines(out):
    return out.getvalue().splitlines()


def join(server, name="scrape"):
    thread = server.threads.get(name)
    if thread is not None:
        thread.join(timeout=5)
        assert not thread.is_alive(), f"{name} thread did not finish"


def canned_scrape(events, result):
    def fake(url, *, out, scope, depth, max_pages, emit, cancel, on_spider, update=False, env=None):
        for event in events:
            emit(event)
        return dict(result, out_dir=out)

    return fake


# ---------- must-reject cases first ----------


@pytest.mark.parametrize(
    "params, reason",
    [
        ({"out": "/tmp/x"}, "missing url"),
        ({"url": "ftp://x/", "out": "/tmp/x"}, "non-http url"),
        ({"url": "docs.example.com", "out": "/tmp/x"}, "relative url"),
        ({"url": URL}, "missing out"),
        ({"url": URL, "out": ""}, "empty out"),
        ({"url": URL, "out": "/tmp/x", "max_pages": "lots"}, "string max_pages"),
        ({"url": URL, "out": "/tmp/x", "depth": True}, "bool depth"),
        ({"url": URL, "out": "/tmp/x", "depth": 1.5}, "float depth"),
        ({"url": URL, "out": "/tmp/x", "scope": "/other/"}, "scope not a prefix"),
        ({"url": URL, "out": "/tmp/x", "scope": 3}, "non-string scope"),
    ],
)
def test_scrape_rejects_bad_params(params, reason):
    """Every bad input is -32602 invalid params — never -32603 internal."""
    server, out = make_server()
    server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "scrape", "params": params})
    (line,) = lines(out)
    error = json.loads(line)["error"]
    assert error["code"] == rpc.INVALID_PARAMS, reason
    assert "scrape" not in server.threads, "no run thread may start on bad params"


def test_protocol_level_rejects():
    server, out = make_server()
    server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "explode"})
    server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "scrape", "params": [1, 2]})
    server.dispatch({"jsonrpc": "2.0", "id": 3})  # no method
    got = [json.loads(line)["error"]["code"] for line in lines(out)]
    assert got == [rpc.METHOD_NOT_FOUND, rpc.INVALID_PARAMS, rpc.INVALID_REQUEST]


def test_parse_error_line():
    server, out = make_server(stdin=io.StringIO("this is not json\n"), exit_fn=lambda c: None)
    server.serve()
    first = json.loads(lines(out)[0])
    assert first["error"]["code"] == rpc.PARSE_ERROR
    assert first["id"] is None


def test_cancel_without_scrape_and_rid_mismatch(monkeypatch):
    server, out = make_server()
    server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "cancel"})
    assert json.loads(lines(out)[0])["error"]["code"] == rpc.NO_SCRAPE

    hold = threading.Event()

    def blocking(url, *, out, scope, depth, max_pages, emit, cancel, on_spider, update=False, env=None):
        hold.wait(timeout=5)
        return {"written": 0, "out_dir": out, "exit_code": 3, "cancelled": True, "manifest": {}}

    monkeypatch.setattr(rpc, "run_scrape", blocking)
    server.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "scrape", "params": {"url": URL, "out": "/tmp/x"}}
    )
    server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "cancel", "params": {"rid": 999}})
    assert json.loads(lines(out)[1])["error"]["code"] == rpc.NO_SCRAPE
    hold.set()
    join(server)


def test_second_scrape_and_second_map_are_busy(monkeypatch):
    server, out = make_server()
    hold = threading.Event()

    def blocking(url, **kwargs):
        hold.wait(timeout=5)
        return {"written": 0, "out_dir": "x", "exit_code": 3, "cancelled": False, "manifest": {}}

    monkeypatch.setattr(rpc, "run_scrape", lambda url, **kw: blocking(url))
    monkeypatch.setattr(rpc, "run_map", lambda url, **kw: blocking(url))
    server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "scrape", "params": {"url": URL, "out": "/tmp/x"}}
    )
    server.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "scrape", "params": {"url": URL, "out": "/tmp/x"}}
    )
    server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "map", "params": {"url": URL}})
    server.dispatch({"jsonrpc": "2.0", "id": 4, "method": "map", "params": {"url": URL}})
    busy = [json.loads(line) for line in lines(out)]
    assert [m["id"] for m in busy] == [2, 4]
    assert all(m["error"]["code"] == rpc.BUSY for m in busy)
    hold.set()
    join(server, "scrape")
    join(server, "map")


# ---------- recorded session, byte-for-byte ----------


def test_recorded_session_byte_for_byte(monkeypatch):
    with open(FIXTURE, encoding="utf-8") as fh:
        session = json.load(fh)

    events = [
        {"type": "platform", "platform": "gitbook", "message": "detected gitbook"},
        {"type": "page", "url": "https://docs.example.com/docs/a", "outcome": "ok"},
    ]
    result = {
        "written": 1,
        "out_dir": "",
        "exit_code": 0,
        "cancelled": False,
        "manifest": {"schema": 1},
    }
    monkeypatch.setattr(rpc, "run_scrape", canned_scrape(events, result))

    def canned_map(url, *, scope, emit):
        emit({"type": "progress", "message": "mapping"})
        return {
            "pages": ["https://docs.example.com/docs/a"],
            "tree": {"total": 1},
            "scope": "/docs/",
            "discovery": "sitemap",
            "platform": "gitbook",
        }

    monkeypatch.setattr(rpc, "run_map", canned_map)

    server, out = make_server()
    expected = []
    for step in session:
        server.dispatch(step["send"])
        join(server, "scrape")
        join(server, "map")
        expected += [line.replace("{VERSION}", __version__) for line in step["expect"]]

    assert lines(out) == expected  # byte-for-byte: same serializer, same order


# ---------- cancel lifecycle ----------


def test_cancel_during_discovery_no_spider_yet(monkeypatch):
    """Cancel lands while run_scrape is in preflight/discovery: no engine exists,
    the cooperative event alone must stop the run — no AttributeError, no hang."""
    server, out = make_server()
    started = threading.Event()

    def discovery_phase(url, *, out, scope, depth, max_pages, emit, cancel, on_spider, update=False, env=None):
        started.set()
        assert cancel.wait(timeout=5), "cancel event never arrived"
        return {"written": 0, "out_dir": out, "exit_code": 3, "cancelled": True, "manifest": {}}

    monkeypatch.setattr(rpc, "run_scrape", discovery_phase)
    server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "scrape", "params": {"url": URL, "out": "/tmp/x"}}
    )
    assert started.wait(timeout=5)
    server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "cancel"})
    join(server)
    got = [json.loads(line) for line in lines(out)]
    cancel_resp = next(m for m in got if m.get("id") == 2)
    scrape_resp = next(m for m in got if m.get("id") == 1)
    assert cancel_resp["result"] == {"cancelling": True, "rid": 1}
    assert scrape_resp["result"]["cancelled"] is True


def test_cancel_twice_is_idempotent_one_engine_pause(monkeypatch):
    server, out = make_server()

    class FakeSpider:
        calls = 0

        def request_cancel(self):
            FakeSpider.calls += 1

    spider = FakeSpider()

    def with_spider(url, *, out, scope, depth, max_pages, emit, cancel, on_spider, update=False, env=None):
        on_spider(spider)
        assert cancel.wait(timeout=5)
        return {"written": 0, "out_dir": out, "exit_code": 1, "cancelled": True, "manifest": {}}

    monkeypatch.setattr(rpc, "run_scrape", with_spider)
    server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "scrape", "params": {"url": URL, "out": "/tmp/x"}}
    )
    server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "cancel"})
    server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "cancel", "params": {"rid": 1}})
    join(server)
    assert FakeSpider.calls == 1, "second cancel must not re-invoke engine pause"
    got = {m["id"]: m for m in map(json.loads, lines(out)) if "id" in m}
    assert got[2]["result"] == got[3]["result"] == {"cancelling": True, "rid": 1}


def test_clamps_reach_runner(monkeypatch):
    seen = {}

    def record(url, *, out, scope, depth, max_pages, emit, cancel, on_spider, update=False, env=None):
        seen.update(depth=depth, max_pages=max_pages)
        return {"written": 0, "out_dir": out, "exit_code": 3, "cancelled": False, "manifest": {}}

    monkeypatch.setattr(rpc, "run_scrape", record)
    server, out = make_server()
    server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "scrape",
            "params": {"url": URL, "out": "/tmp/x", "depth": 99, "max_pages": 0},
        }
    )
    join(server)
    assert seen == {"depth": rpc.DEPTH_MAX, "max_pages": 1}


# ---------- EOF lifecycle ----------


def test_eof_with_live_run_bounded_exit(monkeypatch):
    exited = {}
    started = threading.Event()

    def blocking(url, *, out, scope, depth, max_pages, emit, cancel, on_spider, update=False, env=None):
        started.set()
        assert cancel.wait(timeout=5), "shutdown must set the cancel event"
        emit({"type": "page", "url": "late"})  # lands after EOF: must be swallowed
        return {"written": 0, "out_dir": out, "exit_code": 1, "cancelled": True, "manifest": {}}

    monkeypatch.setattr(rpc, "run_scrape", blocking)
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + "\n"
        + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "scrape", "params": {"url": URL, "out": "/tmp/x"}}
        )
        + "\n"
    )
    server, out = make_server(stdin=stdin, exit_fn=lambda code: exited.setdefault("code", code))
    server.serve()  # reads both lines, hits EOF, shuts down
    assert exited == {"code": 0}
    assert started.is_set()
    got = lines(out)
    assert len(got) == 1  # only the initialize response: post-EOF writes swallowed
    assert json.loads(got[0])["result"]["protocol"] == rpc.PROTOCOL


# ---------- cancelled outcome semantics ----------


def test_cancelled_outcome_is_not_a_failure():
    assert "cancelled" in OUTCOMES
    assert "cancelled" not in FAILURE_OUTCOMES
    ledger = Ledger()
    ledger.record("https://d/a", "ok", chars=500, file="a.md")
    ledger.record("https://d/b", "cancelled", "run cancelled before fetch")
    assert ledger.counts()["cancelled"] == 1
    assert exit_code(ledger, written=1) == 0, "a cancelled remainder must not force exit 1"
    assert ledger.reconcile()


def test_spider_on_close_records_cancelled(tmp_path):
    """The real on_close path: cancelled remainder -> 'cancelled', not failed_fetch;
    a URL with an exhausted HTTP status keeps its real failure class."""
    from iofast_engine.spider import DocSpider

    spider = object.__new__(DocSpider)
    spider.emit = lambda event: None
    spider.start_url = "https://docs.example.com/docs/"
    spider.adapter = None
    spider.scope = "/docs/"
    spider.max_depth = 3
    spider.max_pages = 200
    spider.discovery = "sitemap"
    spider.seeds = ["https://docs.example.com/docs/a"]
    spider.browsers = False
    spider.rescued = None
    spider.out_dir = str(tmp_path)
    spider.manifest = {}
    spider.started = {}
    spider.last_status = {"https://docs.example.com/docs/c": 429}
    spider.cancel_event = threading.Event()
    spider.cancel_event.set()
    spider.ledger = Ledger()
    spider.ledger.record("https://docs.example.com/docs/a", "ok", chars=500, file="a.md")
    (tmp_path / "a.md").write_text("SOURCE\nUNTRUSTED\n" + "x" * 500, encoding="utf-8")
    spider.ledger.enqueue("https://docs.example.com/docs/b")  # never fetched -> cancelled
    spider.ledger.enqueue("https://docs.example.com/docs/c")  # exhausted 429 -> failed_fetch

    asyncio.run(spider.on_close())

    records = spider.ledger.records
    assert records["https://docs.example.com/docs/b"]["outcome"] == "cancelled"
    assert records["https://docs.example.com/docs/c"]["outcome"] == "failed_fetch"
    assert spider.manifest["info"]["cancelled"] is True
    assert spider.manifest["reconciled"] is True
    assert spider.manifest["counts"]["cancelled"] == 1


# ---------- request-scoped secrets ----------


def test_scrape_rejects_disallowed_env_keys():
    """MUST-FAIL: the env allowlist exists to stop arbitrary environment writes."""
    server, out = make_server()
    server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "scrape",
            "params": {"url": URL, "out": "/tmp/x", "env": {"PATH": "/evil"}},
        }
    )
    assert json.loads(lines(out)[0])["error"]["code"] == rpc.INVALID_PARAMS
    assert "scrape" not in server.threads


def test_env_is_request_scoped_and_restored():
    """The secret exists during the run, and is gone from os.environ after."""
    from iofast_engine.runner import run_scrape

    key = "IOFAST_RESCUE_MODEL"
    assert key not in os.environ
    seen = {}
    cancel = threading.Event()
    cancel.set()  # short-circuits to the no-network early-cancel path

    def emit(event):
        if event.get("type") == "result":
            seen["during"] = os.environ.get(key)

    result = run_scrape(URL, emit=emit, cancel=cancel, env={key: "test-model"})
    assert result["cancelled"] is True
    assert seen["during"] == "test-model", "secret must be set while the run executes"
    assert key not in os.environ, "secret must be restored after the run"
