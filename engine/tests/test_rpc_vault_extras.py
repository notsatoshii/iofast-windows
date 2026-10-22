"""RPC tests for vault extras: vault.ask, vault.check, digest.make.

Follows the same pattern as test_rpc_session.py — dispatch directly,
join worker threads, assert response shape.
"""

import io
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from iofast_engine.rpc import RpcServer


def make_server():
    out = io.StringIO()
    server = RpcServer(stdin=None, stdout=out, exit_fn=lambda code: None)
    return server, out


def lines(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def join(server, name, timeout=5):
    thread = server.threads.get(name)
    if thread is not None:
        thread.join(timeout=timeout)
        assert not thread.is_alive(), f"{name} thread did not finish"


class TestVaultAskRpc:
    def test_dispatches_and_responds(self, tmp_path):
        server, out = make_server()
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Test Note.md").write_text(
            "---\ntags: [test]\n---\n# Test Note\nContent here.",
            encoding="utf-8",
        )

        mock_result = {
            "answer": "Test answer about [[Test Note]].",
            "sources": [{"title": "Test Note", "path": str(vault / "Test Note.md"), "score": 5.0}],
            "saved_path": None,
        }

        with patch("iofast_engine.vault.extras_rpc.vault_ask", return_value=mock_result):
            server.dispatch({
                "jsonrpc": "2.0",
                "id": 100,
                "method": "vault.ask",
                "params": {"question": "what is test", "vault": str(vault)},
            })
            join(server, "vault_ask")

        responses = lines(out)
        result_msg = next((r for r in responses if r.get("id") == 100), None)
        assert result_msg is not None
        assert "result" in result_msg
        assert result_msg["result"]["answer"] == "Test answer about [[Test Note]]."
        assert len(result_msg["result"]["sources"]) == 1

    def test_rejects_when_busy(self):
        server, out = make_server()
        # Simulate a running thread
        fake_thread = threading.Thread(target=lambda: None)
        fake_thread.start()
        server.threads["vault_ask"] = fake_thread
        fake_thread.join()  # ensure it's done but still in dict

        # Replace with a live thread
        event = threading.Event()

        def blocker():
            event.wait(timeout=10)

        live_thread = threading.Thread(target=blocker, daemon=True)
        live_thread.start()
        server.threads["vault_ask"] = live_thread

        server.dispatch({
            "jsonrpc": "2.0",
            "id": 101,
            "method": "vault.ask",
            "params": {"question": "test"},
        })

        responses = lines(out)
        error_msg = next((r for r in responses if r.get("id") == 101), None)
        assert error_msg is not None
        assert "error" in error_msg
        assert error_msg["error"]["code"] == -32000  # BUSY

        event.set()
        live_thread.join(timeout=2)


class TestVaultCheckRpc:
    def test_dispatches_and_responds(self, tmp_path):
        server, out = make_server()
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)

        mock_result = {
            "week": "2026-W43",
            "report_path": str(vault / "Weekly" / "2026-W43 Vault check.md"),
            "issues": 5,
            "duplicates": 0,
            "quicknotes": 2,
        }

        with patch("iofast_engine.vault.extras_rpc.vault_check", return_value=mock_result):
            server.dispatch({
                "jsonrpc": "2.0",
                "id": 200,
                "method": "vault.check",
                "params": {"vault": str(vault)},
            })
            join(server, "vault_check")

        responses = lines(out)
        result_msg = next((r for r in responses if r.get("id") == 200), None)
        assert result_msg is not None
        assert "result" in result_msg
        assert result_msg["result"]["week"] == "2026-W43"
        assert result_msg["result"]["issues"] == 5

    def test_rejects_when_busy(self):
        server, out = make_server()
        event = threading.Event()

        def blocker():
            event.wait(timeout=10)

        live_thread = threading.Thread(target=blocker, daemon=True)
        live_thread.start()
        server.threads["vault_check"] = live_thread

        server.dispatch({
            "jsonrpc": "2.0",
            "id": 201,
            "method": "vault.check",
            "params": {},
        })

        responses = lines(out)
        error_msg = next((r for r in responses if r.get("id") == 201), None)
        assert error_msg is not None
        assert "error" in error_msg

        event.set()
        live_thread.join(timeout=2)


class TestDigestMakeRpc:
    def test_dispatches_and_responds(self, tmp_path):
        server, out = make_server()
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)

        mock_result = {
            "week": "2026-W43",
            "digest_path": str(vault / "Weekly" / "2026-W43 Digest.md"),
            "notes_count": 3,
            "log_entries": 2,
        }

        with patch("iofast_engine.vault.extras_rpc.digest_make", return_value=mock_result):
            server.dispatch({
                "jsonrpc": "2.0",
                "id": 300,
                "method": "digest.make",
                "params": {"vault": str(vault)},
            })
            join(server, "digest_make")

        responses = lines(out)
        result_msg = next((r for r in responses if r.get("id") == 300), None)
        assert result_msg is not None
        assert "result" in result_msg
        assert result_msg["result"]["week"] == "2026-W43"
        assert result_msg["result"]["notes_count"] == 3

    def test_rejects_when_busy(self):
        server, out = make_server()
        event = threading.Event()

        def blocker():
            event.wait(timeout=10)

        live_thread = threading.Thread(target=blocker, daemon=True)
        live_thread.start()
        server.threads["digest_make"] = live_thread

        server.dispatch({
            "jsonrpc": "2.0",
            "id": 301,
            "method": "digest.make",
            "params": {},
        })

        responses = lines(out)
        error_msg = next((r for r in responses if r.get("id") == 301), None)
        assert error_msg is not None
        assert "error" in error_msg

        event.set()
        live_thread.join(timeout=2)