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


def test_scope_root_without_trailing_slash_is_in_scope():
    # clean_url strips trailing slashes, so manifests store a scope-root start
    # URL as "https://x.com/docs" beside scope "/docs/". Resume, re-scrape, and
    # the scheduler replay that stored pair; validate must accept it.
    from iofast_engine.runner import validate

    assert validate("https://docs.x.com/docs", scope="/docs/") is None
    assert validate("https://docs.x.com/docs/intro", scope="/docs/") is None
    assert validate("https://docs.x.com/api", scope="/docs/") is not None


def test_estimate_validates_without_a_scope_flag():
    """estimate has no --scope: validation must not crash reading it (offline: bad URL)."""
    result = run("estimate", "ftp://x.com/docs/")
    assert result.returncode == 2 and "AttributeError" not in result.stderr, result.stderr


def test_section_prints_the_folder():
    result = run("section", "https://www.Docs.X.com/docs/intro")
    assert result.returncode == 0 and result.stdout.strip() == "docs-docs-x-com/docs"
