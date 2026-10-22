"""Offline tests for chat history import feature.

Uses fixture zips in engine/tests/fixtures/chat_exports/ — never hits the web.
"""

import json
import tempfile
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "chat_exports"


def test_chatgpt_parse_fixture():
    """Parse the ChatGPT fixture zip and verify structure."""
    from iofast_engine.import_chat.chatgpt import parse_chatgpt_export

    zip_path = FIXTURE_DIR / "chatgpt_export.zip"
    if not zip_path.exists():
        pytest.skip("chatgpt fixture zip not found")

    conversations = parse_chatgpt_export(zip_path, min_messages=4)
    # conv-001 has 4 messages (2 user + 2 assistant), conv-002 has only 2 → skipped
    assert len(conversations) >= 1
    conv = conversations[0]
    assert conv["source"] == "chatgpt"
    assert conv["message_count"] >= 4
    assert conv["char_count"] >= 300
    assert all(m["role"] in ("user", "assistant") for m in conv["messages"])


def test_chatgpt_min_messages_filter():
    """Conversations with fewer than min_messages are skipped."""
    from iofast_engine.import_chat.chatgpt import parse_chatgpt_export

    zip_path = FIXTURE_DIR / "chatgpt_export.zip"
    if not zip_path.exists():
        pytest.skip("chatgpt fixture zip not found")

    # With min_messages=10, everything should be skipped
    conversations = parse_chatgpt_export(zip_path, min_messages=10)
    assert len(conversations) == 0


def test_claude_parse_fixture():
    """Parse the Claude fixture zip and verify structure."""
    from iofast_engine.import_chat.claude import parse_claude_export

    zip_path = FIXTURE_DIR / "claude_export.zip"
    if not zip_path.exists():
        pytest.skip("claude fixture zip not found")

    conversations = parse_claude_export(zip_path, min_messages=4)
    # claude-conv-001 has 4 messages, claude-conv-002-trivial has 2 → skipped
    assert len(conversations) >= 1
    conv = conversations[0]
    assert conv["source"] == "claude"
    assert conv["message_count"] >= 4
    assert conv["char_count"] >= 300


def test_claude_min_messages_filter():
    """Trivial conversations are filtered out."""
    from iofast_engine.import_chat.claude import parse_claude_export

    zip_path = FIXTURE_DIR / "claude_export.zip"
    if not zip_path.exists():
        pytest.skip("claude fixture zip not found")

    conversations = parse_claude_export(zip_path, min_messages=10)
    assert len(conversations) == 0


def test_writer_writes_markdown(tmp_path):
    """Writer produces a markdown file without UNTRUSTED banner."""
    from iofast_engine.import_chat.writer import write_conversation

    conv = {
        "id": "test-001",
        "title": "Test Conversation",
        "date": "2024-10-22",
        "source": "chatgpt",
        "messages": [
            {"role": "user", "text": "Hello"},
            {"role": "assistant", "text": "Hi there! How can I help?"},
        ],
    }
    result = write_conversation(conv, out_root=tmp_path)
    assert result["dry_run"] is False
    out_file = Path(result["path"])
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "UNTRUSTED" not in content
    assert "**You**" in content
    assert "**Assistant**" in content
    assert "# Test Conversation" in content


def test_writer_dry_run(tmp_path):
    """Dry run returns path info but doesn't write."""
    from iofast_engine.import_chat.writer import write_conversation

    conv = {
        "id": "test-002",
        "title": "Dry Run Test",
        "date": "2024-10-22",
        "source": "claude",
        "messages": [{"role": "user", "text": "test"}],
    }
    result = write_conversation(conv, out_root=tmp_path, dry_run=True)
    assert result["dry_run"] is True
    assert not Path(result["path"]).exists()


def test_progress_roundtrip(tmp_path):
    """Progress file saves and loads done IDs correctly."""
    from iofast_engine.import_chat.writer import load_progress, save_progress

    pf = tmp_path / "progress.json"
    assert load_progress(pf) == set()

    save_progress(pf, {"id-1", "id-2"})
    loaded = load_progress(pf)
    assert loaded == {"id-1", "id-2"}


def test_import_resumable(tmp_path):
    """Re-running import skips already-done conversations."""
    from iofast_engine.import_chat.writer import import_conversations

    convos = [
        {
            "id": "c1",
            "title": "First",
            "date": "2024-10-22",
            "source": "chatgpt",
            "messages": [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hi"}],
            "message_count": 2,
            "char_count": 100,
        },
    ]
    pf = tmp_path / "progress.json"

    r1 = import_conversations(convos, source="chatgpt", out_root=tmp_path, progress_file=pf)
    assert r1["written"] == 1
    assert r1["skipped"] == 0

    r2 = import_conversations(convos, source="chatgpt", out_root=tmp_path, progress_file=pf)
    assert r2["written"] == 0
    assert r2["skipped"] == 1


def test_cli_import_chatgpt_dry_run():
    """CLI import chatgpt --dry-run prints counts."""
    from iofast_engine.cli import main

    zip_path = FIXTURE_DIR / "chatgpt_export.zip"
    if not zip_path.exists():
        pytest.skip("chatgpt fixture zip not found")

    rc = main(["import", "chatgpt", str(zip_path), "--dry-run"])
    assert rc == 0


def test_cli_import_claude_dry_run():
    """CLI import claude --dry-run prints counts."""
    from iofast_engine.cli import main

    zip_path = FIXTURE_DIR / "claude_export.zip"
    if not zip_path.exists():
        pytest.skip("claude fixture zip not found")

    rc = main(["import", "claude", str(zip_path), "--dry-run"])
    assert rc == 0