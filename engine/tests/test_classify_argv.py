"""Unit test: verify classify._call_claude_cli uses the correct argv.

Checks that _call_claude_cli invokes `claude` with --tools "" and
--strict-mcp-config (not the nonexistent --no-tools), and that cwd is
the empty %LOCALAPPDATA%/iofast/llm-cwd folder, not the home dir.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_claude_cli_argv():
    """_call_claude_cli must use --tools '' --strict-mcp-config, cwd = llm-cwd."""
    from iofast_engine.vault.classify import _call_claude_cli

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"result": "test json"}'

    with patch("iofast_engine.vault.classify.subprocess.run", return_value=mock_result) as mock_run:
        _call_claude_cli("test prompt")

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    cmd = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("args", [])
    cwd = call_kwargs.kwargs.get("cwd")

    # Verify exact flags
    assert "--tools" in cmd, f"--tools flag missing from {cmd}"
    tools_idx = cmd.index("--tools")
    assert cmd[tools_idx + 1] == "", f"--tools value should be empty string, got {cmd[tools_idx + 1]!r}"
    assert "--strict-mcp-config" in cmd, f"--strict-mcp-config missing from {cmd}"
    assert "--no-tools" not in cmd, f"--no-tools is not a real flag; found in {cmd}"

    # Verify cwd is the empty llm-cwd folder, not home
    expected_cwd = str(Path(os.environ.get("LOCALAPPDATA", ""), "iofast", "llm-cwd"))
    assert cwd == expected_cwd, f"cwd should be {expected_cwd}, got {cwd}"
    assert cwd != str(Path.home()), "cwd must not be the home directory"

    # Verify prompt goes on stdin
    assert call_kwargs.kwargs.get("input") == "test prompt"


def test_claude_cli_argv_no_localappdata(tmp_path, monkeypatch):
    """When LOCALAPPDATA is unset, llm-cwd falls back gracefully."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    from iofast_engine.vault.classify import _call_claude_cli

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"result": "ok"}'

    with patch("iofast_engine.vault.classify.subprocess.run", return_value=mock_result) as mock_run:
        _call_claude_cli("prompt")

    cmd = mock_run.call_args.args[0] if mock_run.call_args.args else mock_run.call_args.kwargs.get("args", [])
    assert "--tools" in cmd
    assert "--strict-mcp-config" in cmd