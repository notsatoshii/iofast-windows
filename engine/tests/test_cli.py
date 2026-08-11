"""CLI exit codes. Validation happens before any network call, so these stay offline."""

import subprocess
import sys

CLI = [sys.executable, "-m", "iofast_engine.cli"]


def run(*args):
    return subprocess.run(CLI + list(args), capture_output=True, text=True, timeout=60)


def test_version_exits_0():
    result = run("--version")
    assert result.returncode == 0, result.stderr
    assert "iofast-engine" in result.stdout


def test_missing_url_exits_2():
    assert run("scrape").returncode == 2


def test_no_subcommand_exits_2():
    assert run().returncode == 2


def test_scope_not_prefixing_url_path_exits_2():
    result = run("scrape", "https://docs.x.com/docs/intro", "--scope", "/api/")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not a path prefix" in result.stderr


def test_non_http_url_exits_2():
    assert run("scrape", "ftp://x.com/docs").returncode == 2
