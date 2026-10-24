"""Phone inbox HTTP contract against a local fake server — offline, no real key.

Covers the four contract cases the desktop poller depends on: pending links,
done, a rejected key (401) and a server that is down. Plus the RPC wrappers.
"""

import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from iofast_engine import inbox
from iofast_engine.rpc import INBOX_AUTH, INTERNAL_ERROR, INVALID_PARAMS, RpcServer

GOOD_KEY = "test-inbox-key-not-real"


class FakeInbox:
    """In-memory server state: pending links and the done calls it received."""

    def __init__(self):
        self.pending = []
        self.done = []
        self.auth_seen = []
        self.fail_with = None  # e.g. 500 to simulate a broken server


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep pytest output clean
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            auth = self.headers.get("Authorization")
            state.auth_seen.append(auth)
            if auth != f"Bearer {GOOD_KEY}":
                self._send(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            if self.path.endswith("/health"):
                return self._send(200, {"ok": True})
            if state.fail_with:
                return self._send(state.fail_with, {"error": "boom"})
            if not self._authed():
                return None
            if self.path.endswith("/links"):
                return self._send(200, {"links": list(state.pending)})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if state.fail_with:
                return self._send(state.fail_with, {"error": "boom"})
            if not self._authed():
                return None
            parts = self.path.rstrip("/").split("/")
            if len(parts) >= 3 and parts[-1] == "done" and parts[-3] == "links":
                link_id = parts[-2]
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                state.done.append((link_id, body))
                state.pending = [l for l in state.pending if l["id"] != link_id]
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})

    return Handler


@pytest.fixture
def fake():
    state = FakeInbox()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_address[1]}/iofast-inbox"
    yield state
    server.shutdown()
    server.server_close()


def dead_base():
    """A localhost port with nothing listening: the 'server down' case."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/iofast-inbox"


LINK_A = {"id": "a1", "url": "https://example.com/post", "source": "android-share", "captured_at": "2026-10-25T10:00:00Z"}
LINK_B = {"id": "b2", "url": "https://youtu.be/xyz", "source": "android-share", "captured_at": "2026-10-25T10:01:00Z"}


# ---------- must-reject cases first ----------


def test_bad_key_raises_auth_error_and_never_leaks_the_key(fake):
    with pytest.raises(inbox.InboxAuthError) as err:
        inbox.fetch_links("wrong-key-123", base=fake.base)
    assert "wrong-key-123" not in str(err.value)


def test_missing_key_is_refused_before_any_request(fake):
    with pytest.raises(inbox.InboxAuthError):
        inbox.fetch_links("  ", base=fake.base)
    assert fake.auth_seen == []


def test_server_down_is_a_network_error(fake):
    with pytest.raises(inbox.InboxNetError):
        inbox.fetch_links(GOOD_KEY, base=dead_base(), timeout=3)
    with pytest.raises(inbox.InboxNetError):
        inbox.mark_done(GOOD_KEY, "a1", True, "x", base=dead_base(), timeout=3)


def test_server_error_is_a_network_error_and_links_stay_pending(fake):
    fake.pending = [LINK_A]
    fake.fail_with = 500
    with pytest.raises(inbox.InboxNetError):
        inbox.mark_done(GOOD_KEY, "a1", True, "saved", base=fake.base)
    assert fake.pending == [LINK_A]


def test_plain_http_off_this_machine_is_refused():
    with pytest.raises(ValueError):
        inbox.fetch_links(GOOD_KEY, base="http://agentos.rest/iofast-inbox")


# ---------- contract ----------


def test_fetch_returns_pending_oldest_first_with_bearer_key(fake):
    fake.pending = [LINK_A, LINK_B, {"id": 3, "url": "https://x.com/s/1"}, {"url": "no-id"}, "junk"]
    links = inbox.fetch_links(GOOD_KEY, base=fake.base)
    assert [l["id"] for l in links] == ["a1", "b2", "3"]
    assert links[0]["url"] == LINK_A["url"]
    assert links[0]["source"] == "android-share"
    assert fake.auth_seen == [f"Bearer {GOOD_KEY}"]


def test_empty_inbox(fake):
    assert inbox.fetch_links(GOOD_KEY, base=fake.base) == []


def test_done_posts_ok_and_note_and_removes_the_link(fake):
    fake.pending = [LINK_A, LINK_B]
    inbox.mark_done(GOOD_KEY, "a1", True, "C:\\vault\\Inbox\\post.md", base=fake.base)
    inbox.mark_done(GOOD_KEY, "b2", False, "RuntimeError: no captions\n" + "x" * 1000, base=fake.base)
    assert fake.done[0] == ("a1", {"ok": True, "note": "C:\\vault\\Inbox\\post.md"})
    assert fake.done[1][0] == "b2"
    assert fake.done[1][1]["ok"] is False
    assert len(fake.done[1][1]["note"]) <= inbox.NOTE_MAX
    assert "\n" not in fake.done[1][1]["note"]
    assert inbox.fetch_links(GOOD_KEY, base=fake.base) == []


def test_done_with_bad_key_is_auth_error(fake):
    fake.pending = [LINK_A]
    with pytest.raises(inbox.InboxAuthError):
        inbox.mark_done("nope", "a1", True, "x", base=fake.base)
    assert fake.pending == [LINK_A]


def test_health_needs_no_key(fake):
    assert inbox.health(base=fake.base) is True
    assert fake.auth_seen == []


# ---------- RPC wrappers ----------


def rpc_call(method, params):
    out = io.StringIO()
    server = RpcServer(stdin=io.StringIO(), stdout=out, exit_fn=lambda code: None)
    thread = getattr(server, f"handle_{method.replace('.', '_')}")(7, params)
    if thread is not None:
        thread.join(timeout=10)
        assert not thread.is_alive()
    reply = json.loads(out.getvalue().splitlines()[-1])
    assert reply["id"] == 7
    return reply


def test_rpc_fetch_and_done_round_trip(fake):
    fake.pending = [LINK_A]
    reply = rpc_call("inbox.fetch", {"key": GOOD_KEY, "base": fake.base})
    assert reply["result"]["links"][0]["id"] == "a1"
    reply = rpc_call("inbox.done", {"key": GOOD_KEY, "base": fake.base, "id": "a1", "ok": True, "note": "saved"})
    assert reply["result"] == {"ok": True}
    assert fake.pending == []


def test_rpc_bad_key_is_inbox_auth_code_without_the_key(fake):
    reply = rpc_call("inbox.fetch", {"key": "leaky-secret", "base": fake.base})
    assert reply["error"]["code"] == INBOX_AUTH
    assert "leaky-secret" not in json.dumps(reply)


def test_rpc_no_key_is_inbox_auth_code():
    assert rpc_call("inbox.fetch", {})["error"]["code"] == INBOX_AUTH


def test_rpc_server_down_is_internal_error():
    reply = rpc_call("inbox.fetch", {"key": GOOD_KEY, "base": dead_base()})
    assert reply["error"]["code"] == INTERNAL_ERROR


def test_rpc_done_validates_params(fake):
    assert rpc_call("inbox.done", {"key": GOOD_KEY, "base": fake.base, "id": "", "ok": True})["error"]["code"] == INVALID_PARAMS
    assert rpc_call("inbox.done", {"key": GOOD_KEY, "base": fake.base, "id": "a1", "ok": "yes"})["error"]["code"] == INVALID_PARAMS
    assert rpc_call("inbox.fetch", {"key": GOOD_KEY, "base": "http://evil.example/x"})["error"]["code"] == INVALID_PARAMS
    assert fake.done == []
